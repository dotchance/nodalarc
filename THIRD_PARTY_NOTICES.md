# Third-Party Notices

This file lists third-party material distributed with the NodalArc source tree or
included in artifacts built from it. NodalArc-owned code and documentation are
licensed under the GNU Affero General Public License, version 3 only
(AGPL-3.0-only), with the additional terms and permissions in `NOTICE`.
Third-party material remains under its own license or usage terms.

## Bundled Frontend Assets

### Inter Font

- Paths: `frontend/public/fonts/Inter-400.woff2`, `frontend/public/fonts/Inter-600.woff2`,
  `frontend/public/fonts/Inter-700.woff2`
- Project: Inter
- Author: The Inter Project Authors
- Source: <https://github.com/rsms/inter>
- License: SIL Open Font License 1.1
- Local license text: `LICENSES/Inter-OFL-1.1.txt`
- Local file hashes:
  - `Inter-400.woff2`: `sha256:8909904ab6c872eb994093482a88a28eca2cd95912d7b6fecd72103b0dc07edc`
  - `Inter-600.woff2`: `sha256:f9a06e79cd3a2a20951c0f0e28f66dd0e6d3fda73911d640a2125c8fcb78f21a`
  - `Inter-700.woff2`: `sha256:6f56409fd3d64bb85f7d070bce20749db2d66b6d63cec586cc22d1c761be2491`

### IBM Plex Mono Font

- Paths: `frontend/public/fonts/IBM-Plex-Mono-400.woff2`,
  `frontend/public/fonts/IBM-Plex-Mono-600.woff2`
- Project: IBM Plex
- Author: IBM Corp.
- Source: <https://github.com/IBM/plex>
- License: SIL Open Font License 1.1
- Local license text: `LICENSES/IBM-Plex-Mono-OFL-1.1.txt`
- Local file hashes:
  - `IBM-Plex-Mono-400.woff2`: `sha256:08949f728dc52d528e69b1667d15c89a5686a4ee9a296ff90983985f99c380f7`
  - `IBM-Plex-Mono-600.woff2`: `sha256:0d1f0b8d0722224e32e9f28261bdc86c79115be73444ae5eceb73976a1bcdf83`

### Lucide Icons

- Path: `frontend/src/ui/icons/lucide.ts` (SVG bodies extracted from `lucide-static`)
- Project: Lucide
- Author: Lucide Icons and Contributors
- Source: <https://lucide.dev> / <https://github.com/lucide-icons/lucide>
- License: ISC
- Local license text: `LICENSES/Lucide-ISC.txt`

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

### Mars Color Texture

- Path: `frontend/public/mars-viking-color.jpg`
- Work: Mars Viking MDIM 2.1 Colorized Global Mosaic, 1 km per pixel JPEG
  (21339x10670)
- Source: USGS Astrogeology Science Center
- Source URL: <https://astrogeology.usgs.gov/search/map/mars_viking_colorized_global_mosaic_232m>
- Direct source file: <https://astrogeology.usgs.gov/ckan/dataset/7131d503-cdc9-45a5-8f83-5126c0fd397e/resource/5ea881c6-01b3-41fa-a7af-42d2131b54f1/download/mars_viking_mdim21_clrmosaic_1km.jpg>
- Source file hash: `sha256:fdfcd335559c3dc67052b7e8a9565d850e336ac0d1f3ea7f5eb7826ffb44ecb2`
- Local file hash: `sha256:e2f9fdb040a8169a6f04cf6931eae32fdcd50500143fa8d3ba84452fd9d44472`
- Changes in NodalArc: resampled to 4096x2048 and metadata removed for
  application packaging; no other image content changes.

### Mars Relief Map

- Path: `frontend/public/mars-mola-height.png`
- Work: Mars Global Surveyor MOLA Mission Experiment Gridded Data Record,
  topography at 16 pixels per degree (`megt90n000eb.img`, 5760x2880, signed
  16-bit heights in metres)
- Source: NASA Planetary Data System Geosciences Node
- Source URL: <https://pds-geosciences.wustl.edu/missions/mgs/megdr.html>
- Direct source file: <https://pds-geosciences.wustl.edu/mgs/mgs-m-mola-5-megdr-l3-v1/mgsl_300x/meg016/megt90n000eb.img>
- Source file hash: `sha256:d18d9b9ab8c5516d02e157dd2cde0f1d0d160c21940e953ba22391269a545e7b`
- Local file hash: `sha256:b4c49aca048b393f875e941e763fb466308ee37ea64eabd6eb8d2432c3fe986c`
- Changes in NodalArc: shifted half a turn in longitude so 180 W is at the
  left edge, as in the color texture; heights from -8,177 m to 21,171 m scaled
  linearly to 8-bit gray; resampled to 2048x1024.

USGS-authored or produced data are in the U.S. public domain; USGS asks that it
be credited as the source.

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

## Ephemeris and Planetary Constants Files

- Paths: `configs/ephemerides/`
- Producer: NASA Jet Propulsion Laboratory (JPL): the Solar System Dynamics
  group and the Navigation and Ancillary Information Facility (NAIF)
- Source: <https://naif.jpl.nasa.gov/pub/naif/generic_kernels/>
- Changes in NodalArc: none; each file is shipped as published, and NodalArc
  checks its SHA-256 hash before reading it.

| File | Content | Source path under `generic_kernels/` | SHA-256 |
| --- | --- | --- | --- |
| `de440s.bsp` | Planetary ephemeris DE440 (short span) | `spk/planets/de440s.bsp` | `c1c7feeab882263fc493a9d5a5b2ddd71b54826cdf65d8d17a76126b260a49f2` |
| `mar099s.bsp` | Mars system ephemeris (Phobos, Deimos) | `spk/satellites/mar099s.bsp` | `997dc93ba640e476da7a494d2237dcdeb145e528db37be8ccee588c615e4e1ff` |
| `earth_latest_high_prec.bpc` | Measured Earth orientation | `pck/earth_latest_high_prec.bpc` | `c329bed712083e0ae7b6ff737b9552fd80b6e111d4a2a7b97394e98fe5df8b7d` |
| `earth_2026_260806_2126_predict.bpc` | Predicted Earth orientation | `pck/earth_2026_260806_2126_predict.bpc` | `9f565ab5aaccd33afe54f3a1b20cb8b136636c31a0bd4c0986fac722b931c280` |
| `moon_pa_de440_200625.bpc` | Lunar orientation (principal axes) | `pck/moon_pa_de440_200625.bpc` | `60cd55aa401ea2ea97360636f567554bfe4e37bb829f901b4460a455dfaf783f` |
| `moon_de440_250416.tf` | Lunar reference frames | `fk/satellites/moon_de440_250416.tf` | `a47c71e9c9f33796bdafb2c9d69a7ee447b6016ecad80f71cd6f3e479f9cf768` |
| `pck00011.tpc` | Planetary sizes, shapes and rotation models | `pck/pck00011.tpc` | `3dff7b1dbeceaa01f25467767d3fa25816051c85d162d1edf04acb310ee28bb1` |
| `gm_de440.tpc` | Gravitational parameters | `pck/gm_de440.tpc` | `924ddf4fb9ead9fe8a1aa55780bcabde40b09d00065d58226e24b68d8092f140` |

NASA and JPL should be acknowledged as the source of these files, and their use
must not imply NASA or JPL endorsement of NodalArc, its authors, or any related
product or service.

## Public Source Data

NodalArc includes orbit, constellation, terminal, site and session catalog data
under `catalog/`. Those files use factual orbital parameters,
gateway locations, antenna counts, and filing references from public sources
where noted in the files themselves, including:

- FCC IBFS satellite and earth-station filings
- ITU/regulatory filing references where noted
- Public SDA, Starlink, Kuiper, OneWeb, and Iridium descriptive materials where
  noted
- Public or internally curated gateway research notes where noted

Facts are not owned by NodalArc. The NodalArc-specific catalog files, schema,
comments, selection, validation logic, and documentation are covered by
AGPL-3.0-only unless a file says otherwise; the catalog files and the
documentation's example sessions are additionally available under CC0-1.0, as
`NOTICE` states.

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
| `asyncssh` | EPL-2.0 OR GPL-2.0-or-later | GPL-2.0-or-later |
| `pyroute2` | Apache-2.0 OR GPL-2.0-or-later | Apache-2.0 |

Current package license families:

| License family | Current packages / artifacts | Local license text |
| --- | --- | --- |
| Apache-2.0 | `nats-py`, `grpcio`, `grpcio-tools`, `kubernetes`, `pyroute2` (elected), `typescript`, `aria-query`, `expect-type`, `xml-name-validator` | `LICENSES/Apache-2.0.txt` |
| GPL-2.0-or-later | `asyncssh` (elected), FRRouting runtime image and FRR packages | `LICENSES/GPL-2.0-or-later.txt` |
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

## Session Images Built From Upstream Programs

NodalArc builds the following session images from the upstream source or
package named, at the pinned release. None is published yet; the shipped
profiles name them by placeholder references. The last column says where the
upstream license text is. NodalArc changes nothing in the upstream programs; its
own additions are the scripts named, which are AGPL-3.0-only. The recipes are in
`images/`.

| Image | Upstream program | Pinned at | License | License text |
| --- | --- | --- | --- | --- |
| `nodalarc/frr` | FRRouting, official image `quay.io/frrouting/frr`, plus the `entrypoint.sh` start script | 10.6.2 | GPL-2.0-or-later | none: the official FRR image, and so this image, carries no license text; the GPL-2.0 text is `LICENSES/GPL-2.0-or-later.txt` in this repository |
| `nodalarc/hdtn` | NASA Glenn HDTN, <https://github.com/nasa/HDTN>, plus the `hdtn-node` launcher | v3.0.0 (commit `7fbe90cdbe3c8c737efaacb5985f881f95fc7d2a`) | Apache-2.0 | `/opt/hdtn/share/doc/hdtn/LICENSE.md` |
| `nodalarc/ion` | NASA JPL ION-DTN, <https://github.com/nasa-jpl/ION-DTN>, plus the `ion-node` launcher | 4.2.0 (commit `568df887cb9f18aa8ec013b1a566d83215e499ae`) | BSD-3-Clause (Caltech's wording); the Jerasure and GF-Complete libraries it ships are BSD-3-Clause and the libbloom code built into it is BSD-2-Clause | `/opt/ion/share/doc/ion/`: `license.txt`, `third-party-licenses.txt`, `jerasure-License.txt`, `gf-complete-License.txt`, `libbloom-LICENSE` |
| `nodalarc/picoquic` | picoquic, <https://github.com/private-octopus/picoquic>, with picotls, <https://github.com/h2o/picotls> | commit `467cb81bcafc652bae2a1c8824b70f12f470039c` | MIT (picoquic, picotls); picotls's minicrypto contains cifra (CC0-1.0) and micro-ecc (BSD-2-Clause) | `/usr/share/doc/picoquic/LICENSE`, `/usr/share/doc/picotls/` |
| `nodalarc/ud3tn-tools` | D3TN ud3tn-utils, <https://gitlab.com/d3tn/ud3tn> | 0.15.0 (PyPI) | BSD-3-Clause OR Apache-2.0; its Python dependencies are listed with their licenses in the image's SBOM | `/usr/local/lib/python3.12/site-packages/ud3tn_utils-0.15.0.dist-info/licenses/LICENSE` |

The uD3TN daemon is not built by NodalArc: the uD3TN profiles reference D3TN's
own published image, `registry.gitlab.com/d3tn/ud3tn-docker-images/ud3tn`, by
digest. uD3TN is licensed under AGPL-3.0.

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
