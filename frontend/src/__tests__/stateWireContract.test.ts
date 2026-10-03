/** The page branches on state-feed vocabularies only as the backend declares them.
 *
 * The backend writes every string field of the state feed's wire models to
 * generated/stateWireVocabularies.json: the values a closed field admits, or null for a field
 * it leaves as an open string. This test walks the page's production source with the TypeScript
 * checker and finds every place a wire object's field is compared with a string literal (===,
 * switch cases, Set.has, Array.includes). Each one must name a closed field and a member of its
 * vocabulary. A page that compares against a value the feed never sends, or branches on a field
 * nothing has closed, fails here by file and line.
 *
 * The interfaces in types.ts that mirror the wire models must also declare each closed field
 * as the same union, so the compiler catches the next stale literal before this test runs.
 */
import { readFileSync } from "node:fs";
import { dirname, join, relative } from "node:path";
import { fileURLToPath } from "node:url";
import ts from "typescript";
import { describe, expect, it } from "vitest";
import vocabularies from "../generated/stateWireVocabularies.json";

type Vocabularies = Record<string, Record<string, string[] | null>>;
const WIRE: Vocabularies = vocabularies.models;
const MODULE_OF: Record<string, string> = vocabularies.modules;

const SRC = join(dirname(fileURLToPath(import.meta.url)), "..");
const ROOT = join(SRC, "..");

function program(): ts.Program {
  const configPath = join(ROOT, "tsconfig.json");
  const config = ts.readConfigFile(configPath, ts.sys.readFile);
  const parsed = ts.parseJsonConfigFileContent(config.config, ts.sys, ROOT);
  const production = parsed.fileNames.filter(
    (file) => !/__tests__|\.test\.tsx?$|[\\/]generated[\\/]/.test(file),
  );
  return ts.createProgram(production, { ...parsed.options, noEmit: true });
}

interface Comparison {
  wireType: string;
  field: string;
  literal: string;
  where: string;
}

function wireTypeNames(type: ts.Type): string[] {
  if (type.isUnion()) return type.types.flatMap(wireTypeNames);
  const name = type.aliasSymbol?.name ?? type.symbol?.name;
  return name && name in WIRE ? [name] : [];
}

function stringLiteral(node: ts.Node): string | null {
  if (ts.isStringLiteral(node) || ts.isNoSubstitutionTemplateLiteral(node)) return node.text;
  return null;
}

function stringMembers(node: ts.Node): string[] | null {
  // ["a", "b"] or new Set(["a", "b"])
  let array: ts.Node = node;
  if (ts.isNewExpression(node) && node.arguments?.length === 1) array = node.arguments[0]!;
  if (!ts.isArrayLiteralExpression(array)) return null;
  const members = array.elements.map(stringLiteral);
  return members.every((member) => member !== null) ? (members as string[]) : null;
}

function comparisons(checker: ts.TypeChecker, file: ts.SourceFile): Comparison[] {
  const found: Comparison[] = [];
  const record = (access: ts.PropertyAccessExpression, literals: string[], at: ts.Node) => {
    const { line } = file.getLineAndCharacterOfPosition(at.getStart());
    for (const wireType of wireTypeNames(checker.getTypeAtLocation(access.expression))) {
      for (const literal of literals) {
        found.push({
          wireType,
          field: access.name.text,
          literal,
          where: `${relative(ROOT, file.fileName)}:${line + 1}`,
        });
      }
    }
  };
  const declaredMembers = (expression: ts.Expression): string[] | null => {
    const direct = stringMembers(expression);
    if (direct) return direct;
    if (!ts.isIdentifier(expression)) return null;
    const symbol = checker.getSymbolAtLocation(expression);
    const declaration = symbol?.valueDeclaration;
    if (declaration && ts.isVariableDeclaration(declaration) && declaration.initializer) {
      return stringMembers(declaration.initializer);
    }
    return null;
  };
  const visit = (node: ts.Node) => {
    if (ts.isBinaryExpression(node)) {
      const operator = node.operatorToken.kind;
      if (
        operator === ts.SyntaxKind.EqualsEqualsEqualsToken ||
        operator === ts.SyntaxKind.ExclamationEqualsEqualsToken ||
        operator === ts.SyntaxKind.EqualsEqualsToken ||
        operator === ts.SyntaxKind.ExclamationEqualsToken
      ) {
        for (const [access, other] of [
          [node.left, node.right],
          [node.right, node.left],
        ] as const) {
          const literal = stringLiteral(other);
          if (ts.isPropertyAccessExpression(access) && literal !== null) {
            record(access, [literal], node);
          }
        }
      }
    }
    if (ts.isSwitchStatement(node) && ts.isPropertyAccessExpression(node.expression)) {
      const literals = node.caseBlock.clauses
        .filter(ts.isCaseClause)
        .map((clause) => stringLiteral(clause.expression))
        .filter((literal): literal is string => literal !== null);
      record(node.expression, literals, node);
    }
    if (
      ts.isCallExpression(node) &&
      ts.isPropertyAccessExpression(node.expression) &&
      (node.expression.name.text === "has" || node.expression.name.text === "includes") &&
      node.arguments.length === 1 &&
      ts.isPropertyAccessExpression(node.arguments[0]!)
    ) {
      const members = declaredMembers(node.expression.expression);
      if (members) record(node.arguments[0] as ts.PropertyAccessExpression, members, node);
    }
    ts.forEachChild(node, visit);
  };
  visit(file);
  return found;
}

describe("the page and the state feed agree on every vocabulary", () => {
  const compiled = program();
  const checker = compiled.getTypeChecker();
  const sources = compiled
    .getSourceFiles()
    .filter((file) => !file.isDeclarationFile && file.fileName.startsWith(SRC));
  const found = sources.flatMap((file) => comparisons(checker, file));

  it("finds the page's comparisons against wire fields", () => {
    expect(found.length).toBeGreaterThan(0);
  });

  it("compares wire fields only against values the backend declares", () => {
    const wrong: string[] = [];
    for (const { wireType, field, literal, where } of found) {
      const fields = WIRE[wireType]!;
      const vocabulary = fields[field];
      if (vocabulary === undefined) continue; // not a string field of the wire model
      if (vocabulary === null) {
        wrong.push(
          `${where}: the page branches on ${wireType}.${field} ("${literal}"), ` +
            `which the backend sends as an open string; close it in ${MODULE_OF[wireType]}`,
        );
      } else if (!vocabulary.includes(literal)) {
        wrong.push(
          `${where}: ${wireType}.${field} is never "${literal}"; the backend sends one of ` +
            vocabulary.join(", "),
        );
      }
    }
    expect(wrong, wrong.join("\n")).toEqual([]);
  });

  it("declares every closed wire vocabulary as the same union in types.ts", () => {
    const typesFile = compiled.getSourceFile(join(SRC, "types.ts"));
    expect(typesFile).toBeDefined();
    const wrong: string[] = [];
    for (const statement of typesFile!.statements) {
      if (!ts.isInterfaceDeclaration(statement) || !(statement.name.text in WIRE)) continue;
      const fields = WIRE[statement.name.text]!;
      for (const member of statement.members) {
        if (!ts.isPropertySignature(member) || !ts.isIdentifier(member.name)) continue;
        const vocabulary = fields[member.name.text];
        if (vocabulary === null || vocabulary === undefined) continue; // open, or not a string
        const declared = member.type
          ? checker
              .getTypeFromTypeNode(member.type)
              .getNonNullableType()
          : undefined;
        const literals = declared
          ? (declared.isUnion() ? declared.types : [declared])
              .filter((t): t is ts.StringLiteralType => t.isStringLiteral())
              .map((t) => t.value)
              .sort()
          : [];
        if (literals.join("|") !== [...vocabulary].sort().join("|")) {
          wrong.push(
            `${statement.name.text}.${member.name.text}: types.ts declares ` +
              `${member.type ? member.type.getText() : "nothing"}; the backend sends ` +
              vocabulary.map((value) => `"${value}"`).join(" | "),
          );
        }
      }
    }
    expect(wrong, wrong.join("\n")).toEqual([]);
  });

  it("reads a vocabulary file the backend wrote", () => {
    // The pytest contract test regenerates the file; this guards a hand edit.
    const text = readFileSync(join(SRC, "generated/stateWireVocabularies.json"), "utf-8");
    expect(JSON.parse(text).models).toEqual(WIRE);
  });
});
