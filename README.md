# Vandebron Energie

[![GitHub Release][releases-shield]][releases]
[![GitHub Activity][commits-shield]][commits]
[![License][license-shield]](LICENSE)
[![hacs][hacsbadge]][hacs]

A Home Assistant integration for [Vandebron](https://vandebron.nl) energy data.

> [!WARNING]
> **Not officially supported — use at your own risk.** This is an unofficial community
> integration. Vandebron does not provide support for it and does not guarantee that it will
> keep working. It logs in through a public OAuth client (`home-assistant`) on Vandebron's
> identity provider and uses the same API as mijn.vandebron.nl, and either may change or be
> withdrawn at any time without notice.

## Features

- Monitors current power usage
- Tracks total energy consumption
- Configurable update interval
- Full UI-based setup — no YAML needed

## Installation via HACS

1. Open HACS in Home Assistant.
2. Go to **Integrations** → click the three-dot menu → **Custom repositories**.
3. Add `https://github.com/epodegrid/vandebron_energie_homeassistant` with category **Integration**.
4. Search for **Vandebron Energie** and install it.
5. Restart Home Assistant.

## Manual Installation

1. Copy `custom_components/vandebron_energie` into your HA `config/custom_components/` directory.
2. Restart Home Assistant.

## Configuration

1. Go to **Settings** → **Devices & Services** → **Add Integration**.
2. Search for **Vandebron Energie**.
3. You are sent to the Vandebron login page. Log in there, including the two-factor code
   if your account requires it, and you are returned to Home Assistant.

The login redirects via [My Home Assistant](https://my.home-assistant.io), so set your
Home Assistant URL there first. Your password is never stored, only OAuth tokens.

### Upgrading from a password-based setup

Existing entries show a re-authentication prompt (**Settings** → **Devices & Services**).
Log in once in the browser; the old stored password is removed afterwards.

## Development

```bash
# Install dev dependencies
pip install -r requirements_test.txt

# Run tests
pytest tests/
```

---

[commits-shield]: https://img.shields.io/github/commit-activity/y/epodegrid/vandebron_energie_homeasssistant.svg
[commits]: https://github.com/epodegrid/vandebron_energie_homeasssistant/commits/main
[hacs]: https://hacs.xyz
[hacsbadge]: https://img.shields.io/badge/HACS-Custom-orange.svg
[license-shield]: https://img.shields.io/github/license/epodegrid/vandebron_energie_homeasssistant.svg
[releases-shield]: https://img.shields.io/github/release/epodegrid/vandebron_energie_homeasssistant.svg
[releases]: https://github.com/epodegrid/vandebron_energie_homeasssistant/releases
