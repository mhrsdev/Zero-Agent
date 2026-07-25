# Third-Party Notices

Zero is distributed under the Apache License 2.0 (see `LICENSE`). It depends on
the third-party packages below, each under its own license. This report is
generated from the resolved environment, not hand-maintained.

## Direct runtime dependencies

| Package | Version | License | Project |
|---|---|---|---|
| aiogram | 3.13.1 | MIT | https://aiogram.dev/ |
| aiosqlite | 0.20.0 | MIT License | https://aiosqlite.omnilib.dev |
| pydantic | 2.9.2 | MIT | https://github.com/pydantic/pydantic |
| pytest | 8.3.2 | MIT License | https://docs.pytest.org/en/latest/ |
| pytest-asyncio | 0.24.0 | Apache Software License | https://github.com/pytest-dev/pytest-asyncio |
| python-dotenv | 1.0.1 | BSD License | https://github.com/theskumar/python-dotenv |
| PyYAML | 6.0.2 | MIT License | https://pyyaml.org/ |
| Telethon | 1.44.0 | MIT | https://telethon.dev |

## Full resolved dependency set

| Package | Version | License | Project |
|---|---|---|---|
| aiofiles | 24.1.0 | Apache Software License | https://github.com/Tinche/aiofiles |
| aiogram | 3.13.1 | MIT | https://aiogram.dev/ |
| aiohappyeyeballs | 2.7.1 | Python Software Foundation License | https://github.com/aio-libs/aiohappyeyeballs |
| aiohttp | 3.10.11 | Apache Software License | https://github.com/aio-libs/aiohttp |
| aiosignal | 1.4.0 | Apache Software License | https://github.com/aio-libs/aiosignal |
| aiosqlite | 0.20.0 | MIT License | https://aiosqlite.omnilib.dev |
| annotated-types | 0.8.0 | MIT | https://github.com/annotated-types/annotated-types |
| attrs | 26.1.0 | MIT | https://www.attrs.org/en/stable/changelog.html |
| certifi | 2026.7.22 | Mozilla Public License 2.0 (MPL 2.0) | https://github.com/certifi/python-certifi |
| colorama | 0.4.6 | BSD License | https://github.com/tartley/colorama |
| frozenlist | 1.8.0 | Apache-2.0 | https://github.com/aio-libs/frozenlist |
| idna | 3.18 | BSD-3-Clause | https://github.com/kjd/idna |
| iniconfig | 2.3.0 | MIT | https://github.com/pytest-dev/iniconfig |
| magic-filter | 1.0.12 | MIT | https://github.com/aiogram/magic-filter |
| multidict | 6.7.1 | Apache License 2.0 | https://github.com/aio-libs/multidict |
| packaging | 26.2 | Apache-2.0 OR BSD-2-Clause | https://github.com/pypa/packaging |
| pluggy | 1.6.0 | MIT License | UNKNOWN |
| propcache | 0.5.2 | Apache Software License | https://github.com/aio-libs/propcache |
| pyaes | 1.6.1 | MIT License | https://github.com/ricmoo/pyaes |
| pyasn1 | 0.6.4 | BSD-2-Clause | https://github.com/pyasn1/pyasn1 |
| pydantic | 2.9.2 | MIT | https://github.com/pydantic/pydantic |
| pydantic_core | 2.23.4 | MIT License | https://github.com/pydantic/pydantic-core |
| pytest | 8.3.2 | MIT License | https://docs.pytest.org/en/latest/ |
| pytest-asyncio | 0.24.0 | Apache Software License | https://github.com/pytest-dev/pytest-asyncio |
| python-dotenv | 1.0.1 | BSD License | https://github.com/theskumar/python-dotenv |
| PyYAML | 6.0.2 | MIT License | https://pyyaml.org/ |
| rsa | 4.9.1 | Apache Software License | https://stuvel.eu/rsa |
| Telethon | 1.44.0 | MIT | https://telethon.dev |
| typing_extensions | 4.16.0 | PSF-2.0 | https://github.com/python/typing_extensions |
| tzdata | 2026.3 | Apache-2.0 | https://github.com/python/tzdata |
| yarl | 1.24.5 | Apache-2.0 | https://github.com/aio-libs/yarl |

## License distribution

- MIT: 7
- Apache Software License: 6
- MIT License: 6
- Apache-2.0: 3
- BSD License: 2
- Apache License 2.0: 1
- Apache-2.0 OR BSD-2-Clause: 1
- BSD-2-Clause: 1
- BSD-3-Clause: 1
- Mozilla Public License 2.0 (MPL 2.0): 1
- PSF-2.0: 1
- Python Software Foundation License: 1

## Compatibility

All resolved licenses are permissive or weak-copyleft and are compatible with
distributing Zero under Apache-2.0. `certifi` is MPL-2.0, which is file-level
copyleft: it is redistributed unmodified as a separate package and imposes no
condition on Zero's own source.

Regenerate with:

```bash
python -m piplicenses --format=markdown --with-urls
```
