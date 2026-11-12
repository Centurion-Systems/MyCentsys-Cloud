# Contributing

Thanks for helping improve MyCentsys Cloud for Home Assistant. Bug reports, ideas and pull
requests are welcome.

## Reporting issues

Use the [issue tracker](https://github.com/Centurion-Systems/MyCentsys-Cloud/issues). Include your
Home Assistant version, the integration version, the device type, and if you can a debug log
(see [Troubleshooting](docs/troubleshooting.md)). Never post your phone number, PIN or session
details.

## Development setup

You need Python 3.12 or 3.13. The tests run without a Home Assistant installation.

```bash
python -m venv .venv
```

Activate the environment (`.venv\Scripts\activate` on Windows, `source .venv/bin/activate`
elsewhere), then:

```bash
python -m pip install -r requirements_test.txt
```

```bash
python -m pytest
```

## Project layout

- `custom_components/mycentsys_cloud/`: the integration (Home Assistant platforms, config flow and
  coordinators).
- `custom_components/mycentsys_cloud/cloud/`: the client for the MyCentsys cloud, independent of
  Home Assistant.
- `custom_components/mycentsys_cloud/brand/`: the icon and logo Home Assistant shows (2026.3 or
  newer).
- `tests/`: the test suite.
- `docs/`: user documentation linked from the README.

## Pull requests

1. Fork the repository and create a branch from `main`.
2. Keep each pull request to one change, with tests for new behaviour and a regression test for
   each bug fix.
3. Run the tests locally. Every pull request also runs them on Python 3.12 and 3.13, along with
   Home Assistant's hassfest and the HACS validation, and all must pass.
4. Describe the change for users in `CHANGELOG.md`.
5. Never commit credentials, phone numbers or tokens, and never log them.

By contributing you agree that your contribution is licensed under the [MIT licence](LICENSE).
