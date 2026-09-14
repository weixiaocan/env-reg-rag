# Third-party notices

This repository contains original project source code and configuration. It does not vendor Python packages, model weights, or Qdrant binaries. It does bundle 27 third-party PDF files under `data/raw/`: 23 original corpus candidates and 4 official-verification copies, representing 22 unique byte contents and 5 exact duplicate copies. Bundling a PDF does not mean it has passed formal-corpus admission.

The project itself is distributed under GNU Affero General Public License version 3 only (`AGPL-3.0-only`) because its PDF ingestion path directly uses PyMuPDF under PyMuPDF's open-source AGPL option. A commercial PyMuPDF license is not included.

The project AGPL applies only to project-authored code and documentation. It does not license, sublicense, or change the rights status of third-party PDFs. Public availability of a source is not a claim of redistribution authorization, and the project's learning, research, engineering-validation, and non-commercial-demo purpose is not a substitute for permission. The project does not sell the PDFs or grant commercial-use rights. See `DATA_NOTICE.md` for the data boundary, SHA-256 limitation, correction and takedown process, and contribution restrictions.

## Direct Python dependencies

| Component | Pinned version | Declared license |
| --- | ---: | --- |
| PyMuPDF | 1.28.2 | GNU AGPL 3.0 or Artifex commercial license |
| pypdf | 6.16.2 | BSD-3-Clause |
| paddlepaddle | 3.3.1 | Apache-2.0 |
| paddleocr | 3.7.0 | Apache-2.0 |
| paddlex | 3.7.2 | Apache-2.0 |
| docling | 2.126.0 | MIT |
| qdrant-client | 1.19.0 | Apache-2.0 |
| torch | 2.14.0 | BSD-style and bundled component notices; see the installed distribution |
| transformers | 5.16.1 | Apache-2.0 |
| langchain-core | 1.6.2 | MIT |
| langchain-openai | 1.6.0 | MIT |
| pydantic | 2.13.5 | MIT |
| python-dotenv | 1.2.3 | BSD-3-Clause |
| fastapi | 0.141.1 | MIT |
| starlette | 1.6.0 | BSD-3-Clause |
| uvicorn | 0.52.4 | BSD-3-Clause |
| httpx | 0.28.1 | BSD-3-Clause |
| mcp | 2.2.0 | MIT |

## Separately downloaded runtime components

| Component | Pinned version or identifier | Declared license |
| --- | --- | --- |
| Qdrant server image | `qdrant/qdrant:v1.19.1` | Apache-2.0 |
| BGE embedding model | `BAAI/bge-small-zh-v1.5` | MIT |
| Python base image | `python:3.11-slim` | Multiple licenses; image and Debian package notices apply |

License metadata above is an inventory, not a replacement for upstream license texts. Before redistributing a built container image, collect the complete transitive dependency and operating-system package notices from that exact image.

Upstream license references:

- PyMuPDF: <https://pymupdf.readthedocs.io/en/latest/about.html#license-and-copyright>
- Qdrant: <https://github.com/qdrant/qdrant/blob/master/LICENSE>
- BGE model card: <https://huggingface.co/BAAI/bge-small-zh-v1.5>
- GNU AGPL v3: <https://www.gnu.org/licenses/agpl-3.0.html>
