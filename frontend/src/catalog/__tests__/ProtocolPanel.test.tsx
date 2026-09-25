import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { ExtensionsPanel, ProtocolSelection } from "../ProtocolPanel";
import type { ExtensionRules, RoutingTimers } from "../wizardTypes";

afterEach(cleanup);

const TIMERS: RoutingTimers = {
  bfd: false,
  bfd_detect_multiplier: 3,
  bfd_rx_interval: 300,
  bfd_tx_interval: 300,
  isis_hello_interval: 1,
  isis_hello_multiplier: 3,
  spf_init_delay: 50,
  spf_short_delay: 200,
  spf_long_delay: 1000,
  spf_holddown: 2000,
  spf_time_to_learn: 500,
  ospf_hello_interval: 1,
  ospf_dead_interval: 3,
  ospf_spf_delay: 50,
  ospf_spf_initial_hold: 200,
  ospf_spf_max_hold: 1000,
};

const RULES: ExtensionRules = {
  protocols: [
    {
      id: "ospf",
      label: "Backend OSPF Label",
      description: "Backend OSPF description",
      extensions: ["te"],
      extension_constraints: {},
      timer_label: "Backend OSPF Timers",
      timer_fields: [
        {
          id: "ospf_spf_delay",
          label: "Backend SPF Delay",
          unit: "ms",
          description: "Backend timer description",
          guidance: "Backend timer guidance",
          minimum: 0,
        },
      ],
      bfd_timer_fields: [
        {
          id: "bfd_detect_multiplier",
          label: "Backend Detect Field",
          unit: null,
          description: "Backend detect description",
          guidance: "Backend detect guidance",
          minimum: 1,
          maximum: 255,
        },
        {
          id: "bfd_rx_interval",
          label: "Backend RX Field",
          unit: "backend-ms",
          description: "Backend RX description",
          guidance: "Backend RX guidance",
          minimum: 10,
          maximum: 4294967,
        },
        {
          id: "bfd_tx_interval",
          label: "Backend TX Field",
          unit: "backend-ms",
          description: "Backend TX description",
          guidance: "Backend TX guidance",
          minimum: 11,
          maximum: 4294966,
        },
      ],
      area_strategies: ["flat"],
      default_area_strategy: "flat",
    },
    {
      id: "isis",
      label: "Backend IS-IS Label",
      description: "Backend IS-IS description",
      extensions: [],
      extension_constraints: {},
      timer_label: "Backend IS-IS Timers",
      timer_fields: [
        {
          id: "isis_hello_interval",
          label: "Backend Hello",
          unit: "s",
          description: "Backend hello description",
          guidance: "Backend hello guidance",
          minimum: 1,
        },
      ],
      bfd_timer_fields: null,
      area_strategies: ["flat", "per_plane"],
      default_area_strategy: "flat",
    },
  ],
  extensions: [
    { id: "te", label: "Backend TE Label", description: "Backend TE description" },
  ],
  bfd: {
    heading: "Backend BFD Heading",
    enabled_field: "bfd",
    enable_label: "Backend BFD Enable",
    enable_description: "Backend BFD enable description",
  },
  routing_timer_defaults: TIMERS,
};

describe("ProtocolPanel backend authority", () => {
  it("renders protocol and extension presentation facts from VS-API", () => {
    const onSelect = vi.fn();
    const { rerender } = render(
      <ProtocolSelection selected={null} rules={RULES} onSelect={onSelect} />,
    );

    fireEvent.click(screen.getByRole("button", { name: /Backend OSPF Label/ }));
    expect(onSelect).toHaveBeenCalledWith("ospf");

    rerender(
      <ExtensionsPanel
        protocol="ospf"
        extensions={[]}
        areaStrategy="flat"
        rules={RULES}
        routingTimers={TIMERS}
        onToggleExtension={vi.fn()}
        onSetAreaStrategy={vi.fn()}
        onUpdateTimers={vi.fn()}
        isExtensionAllowed={() => true}
        isExtensionEnabled={() => true}
      />,
    );

    expect(screen.getByText("Backend TE Label")).toBeTruthy();
    // The area strategy choices are the selected protocol's own.
    expect(
      screen.getAllByRole("option").map((option) => option.textContent),
    ).toEqual(["flat"]);
    expect(screen.getByText("Backend OSPF Timers")).toBeTruthy();
    expect(screen.getByText("Backend SPF Delay")).toBeTruthy();
    expect(screen.queryByText("Open Shortest Path First")).toBeNull();
  });

  it("updates the timer field named by backend metadata", () => {
    const onUpdateTimers = vi.fn();
    render(
      <ExtensionsPanel
        protocol="ospf"
        extensions={[]}
        areaStrategy="flat"
        rules={RULES}
        routingTimers={TIMERS}
        onToggleExtension={vi.fn()}
        onSetAreaStrategy={vi.fn()}
        onUpdateTimers={onUpdateTimers}
        isExtensionAllowed={() => true}
        isExtensionEnabled={() => true}
      />,
    );

    fireEvent.change(screen.getByRole("spinbutton", { name: "" }), {
      target: { value: "75" },
    });
    expect(onUpdateTimers).toHaveBeenCalledWith({ ospf_spf_delay: 75 });
  });

  it("renders and updates BFD controls entirely from backend metadata", () => {
    const onUpdateTimers = vi.fn();
    const props = {
      protocol: "ospf" as const,
      extensions: [],
      areaStrategy: "flat" as const,
      rules: RULES,
      onToggleExtension: vi.fn(),
      onSetAreaStrategy: vi.fn(),
      onUpdateTimers,
      isExtensionAllowed: () => true,
      isExtensionEnabled: () => true,
    };
    const { rerender } = render(
      <ExtensionsPanel {...props} routingTimers={TIMERS} />,
    );

    expect(screen.getByText("Backend BFD Heading")).toBeTruthy();
    expect(screen.getByText("Backend BFD enable description")).toBeTruthy();
    fireEvent.click(screen.getByRole("checkbox", { name: /Backend BFD Enable/ }));
    expect(onUpdateTimers).toHaveBeenCalledWith({ bfd: true });

    rerender(
      <ExtensionsPanel {...props} routingTimers={{ ...TIMERS, bfd: true }} />,
    );
    expect(screen.getByText("Backend Detect Field")).toBeTruthy();
    expect(screen.getByText("Backend detect guidance")).toBeTruthy();
    expect(screen.getByText("Backend RX Field")).toBeTruthy();
    expect(screen.getAllByText("backend-ms")).toHaveLength(2);
    expect(screen.getByText("Backend TX description")).toBeTruthy();
  });

  it("bounds each BFD control by the range the backend says the runtime renders", () => {
    render(
      <ExtensionsPanel
        protocol="ospf"
        extensions={[]}
        areaStrategy="flat"
        rules={RULES}
        routingTimers={{ ...TIMERS, bfd: true }}
        onToggleExtension={vi.fn()}
        onSetAreaStrategy={vi.fn()}
        onUpdateTimers={vi.fn()}
        isExtensionAllowed={() => true}
        isExtensionEnabled={() => true}
      />,
    );

    const bounds = screen
      .getAllByRole("spinbutton")
      .slice(1)
      .map((input) => [input.getAttribute("min"), input.getAttribute("max")]);
    expect(bounds).toEqual([
      ["1", "255"],
      ["10", "4294967"],
      ["11", "4294966"],
    ]);
  });

  it("offers no BFD controls for a protocol the runtime renders no BFD for", () => {
    render(
      <ExtensionsPanel
        protocol="isis"
        extensions={[]}
        areaStrategy="flat"
        rules={RULES}
        routingTimers={{ ...TIMERS, bfd: true }}
        onToggleExtension={vi.fn()}
        onSetAreaStrategy={vi.fn()}
        onUpdateTimers={vi.fn()}
        isExtensionAllowed={() => true}
        isExtensionEnabled={() => true}
      />,
    );

    expect(screen.getByText("Backend IS-IS Timers")).toBeTruthy();
    expect(screen.queryByText("Backend BFD Heading")).toBeNull();
    expect(screen.queryByText("Backend Detect Field")).toBeNull();
  });
});
