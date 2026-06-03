# Third-Party Notices

This file lists third-party material distributed with the NodalArc source tree or
included in artifacts built from it. The Apache License 2.0 applies to
NodalArc-owned code and documentation. Third-party material remains under its
own license or usage terms.

## Bundled Frontend Assets

### Inter Font

- Path: `frontend/public/fonts/Inter.woff2`
- Project: Inter
- Author: The Inter Project Authors
- Source: <https://github.com/rsms/inter>
- License: SIL Open Font License 1.1
- Local license text: `LICENSES/Inter-OFL-1.1.txt`
- Local file hash: `sha256:8af7bd5b545567adffb3dfceb5bedb353a522d7bf1b3a2b8af7b6064156babc0`

### Earth Day Texture

- Path: `frontend/public/earth-blue-marble.jpg`
- Work: Blue Marble: Next Generation with Topography and Bathymetry, January,
  5400x2700 JPEG
- Source: NASA Earth Observatory / NASA Science
- Source URL: <https://science.nasa.gov/earth/earth-observatory/blue-marble-next-generation/base-topography-bathymetry/>
- Direct file: <https://assets.science.nasa.gov/content/dam/science/esd/eo/images/bmng/bmng-topography-bathymetry/january/world.topo.bathy.200401.3x5400x2700.jpg>
- NASA media usage guidelines: <https://www.nasa.gov/multimedia/guidelines/index.html>
- Local file hash: `sha256:1684c4f8f51970dcb4a7451302bf3be17bed657aed9fece6f80d7b191e8afa3d`
- Changes in NodalArc: renamed for application packaging; no image content
  changes.

### Earth Night Texture

- Path: `frontend/public/earth-night.jpg`
- Work: Earth at Night / Black Marble flat map, 2016 Color, 13500x6750 JPEG
- Source: NASA Earth Observatory / NASA Science
- Source URL: <https://science.nasa.gov/earth/earth-observatory/earth-at-night/maps/>
- NASA media usage guidelines: <https://www.nasa.gov/multimedia/guidelines/index.html>
- Local file hash: `sha256:230aac448ae68c358be433dd518888cccb3a85ccf66f7b44326441c324ad6725`

### Moon Color Texture

- Path: `frontend/public/moon-lroc-color.jpg`
- Work: CGI Moon Kit 2025 LROC color map, converted from the 4096x2048
  16-bit sRGB TIFF to a 4096x2048 JPEG for application packaging
- Source: NASA's Scientific Visualization Studio
- Source URL: <https://svs.gsfc.nasa.gov/4720>
- Direct source file: <https://svs.gsfc.nasa.gov/vis/a000000/a004700/a004720/lroc_color_16bit_srgb_4k.tif>
- NASA media usage guidelines: <https://www.nasa.gov/multimedia/guidelines/index.html>
- Local file hash: `sha256:021d1ae5daa7466b3d177f62c618aea6eb8b3f27d245e59b8637936deca6ffb9`
- Changes in NodalArc: converted from TIFF to JPEG and renamed for
  application packaging; no other image content changes.

NASA content is generally not subject to copyright in the United States when used
factually, but NASA should be acknowledged as the source and use must not imply
NASA endorsement of NodalArc, its authors, or any related product or service.

### Natural Earth Country Boundaries

- Path: `frontend/public/ne_110m_countries.geojson`
- Dataset: Natural Earth 1:110m Admin 0 countries
- Source: <https://www.naturalearthdata.com/>
- Terms: <https://www.naturalearthdata.com/about/terms-of-use/>
- Local file hash: `sha256:6866c877d39cba9c357620878839b336d569f8c662d3cfab4cb1dbe2d39c977f`

Natural Earth data is public domain. Citation requested by the project:
"Made with Natural Earth. Free vector and raster map data @ naturalearthdata.com."

## Public Source Data

NodalArc includes constellation, satellite type, scenario, and ground-station
configuration data under `configs/`. Those files use factual orbital parameters,
gateway locations, antenna counts, and filing references from public sources
where noted in the files themselves, including:

- FCC IBFS satellite and earth-station filings
- ITU/regulatory filing references where noted
- Public SDA, Starlink, Kuiper, OneWeb, and Iridium descriptive materials where
  noted
- Public or internally curated gateway research notes where noted

Facts are not owned by NodalArc. The NodalArc-specific configuration files,
schema, comments, selection, validation logic, and documentation remain covered
by the Apache License 2.0 unless a file says otherwise.

## Package Dependencies

NodalArc resolves third-party package dependencies through standard package
manifests and lockfiles:

- Python: `pyproject.toml`, `uv.lock`
- Frontend: `frontend/package.json`, `frontend/package-lock.json`

These package dependencies are not vendored into the source tree, but built
frontend bundles, Python wheels, and container images may include third-party
software. The table below is the source-tree baseline for current direct and
known transitive package licenses. Release artifacts should still include a
generated dependency license report for the exact artifact being distributed.

For dependencies that offer more than one license, NodalArc uses the following
license election when building and distributing project artifacts:

| Package | Available licenses | NodalArc election |
| --- | --- | --- |
| `asyncssh` | EPL-2.0 OR GPL-2.0-or-later | EPL-2.0 |
| `pyroute2` | Apache-2.0 OR GPL-2.0-or-later | Apache-2.0 |

Current package license families:

| License family | Current packages / artifacts | Local license text |
| --- | --- | --- |
| Apache-2.0 | `nats-py`, `grpcio`, `grpcio-tools`, `kubernetes`, `pyroute2` (elected), `typescript`, `aria-query`, `expect-type`, `xml-name-validator` | `LICENSES/Apache-2.0.txt` |
| EPL-2.0 | `asyncssh` (elected) | `LICENSES/EPL-2.0.txt` |
| GPL-2.0-or-later | FRRouting runtime image and FRR packages | `LICENSES/GPL-2.0-or-later.txt` |
| BSD-3-Clause | `numpy`, `jinja2`, `uvicorn`, `websockets`, `protobuf`, `httpx`, `source-map-js`, `tough-cookie`, `@webgpu/types` | `LICENSES/BSD-3-Clause.txt` |
| BSD-2-Clause | `entities`, `webidl-conversions` | `LICENSES/BSD-2-Clause.txt` |
| MIT | `pydantic`, `fastapi`, `sgp4`, `skyfield`, `react`, `react-dom`, `three`, `@xterm/xterm`, Vite/Rollup/esbuild ecosystem packages, D3 packages | `LICENSES/MIT.txt` |
| MIT-0 | `@csstools/color-helpers`, `@csstools/css-syntax-patches-for-csstree` | `LICENSES/MIT-0.txt` |
| ISC | D3 packages and npm transitives recorded as ISC | `LICENSES/ISC.txt` |
| MPL-2.0 | `certifi` | `LICENSES/MPL-2.0.txt` |
| CC-BY-4.0 | `caniuse-lite` | `LICENSES/CC-BY-4.0.txt` |
| CC0-1.0 | `mdn-data` | `LICENSES/CC0-1.0.txt` |
| Unlicense | `robust-predicates` | `LICENSES/Unlicense.txt` |
| BlueOak-1.0.0 | `lru-cache` | `LICENSES/BlueOak-1.0.0.txt` |

Package-specific copyright notices remain in package metadata and should be
carried into generated binary artifact reports. For GPL-licensed FRR packages,
published container images should also provide or link to the corresponding
source code in the way required by the package distributor and GPL terms.

## Container Base Images and System Packages

Docker images built from this repository inherit third-party software and license
terms from their base images and installed operating-system packages. Published
container images should carry or link to an SBOM and generated license report for
the exact image digest being published.

## Trademarks

NodalArc uses third-party names only for descriptive or compatibility purposes.
Starlink, SpaceX, Amazon Kuiper, OneWeb, Iridium, FRRouting, Kubernetes, Docker,
Helm, NATS, NASA, Natural Earth, and other referenced names, marks, and logos are
the property of their respective owners. Their mention does not imply
endorsement, sponsorship, or affiliation.
