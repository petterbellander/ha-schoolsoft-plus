# SchoolSoft Plus for Home Assistant

A custom integration that fetches information from the Swedish school platform [SchoolSoft](https://www.schoolsoft.se/) using a guardian account: schedule, homework, tests, absence, news and school lunch, for every child linked to the account.

## Built on kanylbullen's projects

This integration builds on two projects by [kanylbullen](https://github.com/kanylbullen):

- **[ha-schoolsoft](https://github.com/kanylbullen/ha-schoolsoft)**, the original Home Assistant integration for SchoolSoft (lunch). It served as the model for this integration.
- **[schoolsoft-mcp](https://github.com/kanylbullen/schoolsoft-mcp)**: the client and the parsers for SchoolSoft's pages are included in `custom_components/schoolsoft_plus/vendor/` (MIT licence, see [vendor/LICENSE](custom_components/schoolsoft_plus/vendor/LICENSE)). The client has been modified to use Home Assistant's HTTP client.

Thanks to kanylbullen for the groundwork. The domain is `schoolsoft_plus` so it can be installed alongside ha-schoolsoft (`schoolsoft`) without conflicts.

## What you get

Per child:

| Type | Entity |
|---|---|
| Calendar | Schedule, Homework and assignments, Tests, Lunch |
| Sensor | School start today, School end today, School start tomorrow |
| Sensor | Next test (upcoming tests as attribute), Open assignments |
| Sensor | Unreported absence, Subjects with warnings |
| Sensor | Lunch today (vegetarian option and tomorrow's lunch as attributes) |
| Sensor | Latest news |
| Sensor | After-school (fritids) pick-up today (only if the child is enrolled) |

Per school (if the school links a menu under "Matsedel" on the School information page):

| Type | Entity |
|---|---|
| Sensor | Weekly menu (week number, days in the `dagar` attribute) |
| Image | Menu (when the menu is an image) |

Entity names and attributes are in Swedish, matching SchoolSoft.

Schedule and assignments update every 30 minutes; lunch, assessments, absence and news every 3 hours.

## Dashboard examples

See [docs/cards.md](docs/cards.md) for ready-to-use cards: today's lessons, upcoming tests, open assignments, the week's lunch and alerts for absence and warnings.

## Menus as PDF or image

Some schools don't publish lunch in SchoolSoft but link a weekly menu instead, often a file on Google Drive. The integration downloads it:

- **PDF** is parsed as text. Both simple layouts ("Måndag: Köttbullar … Vegetariskt: …") and layouts with a theme per day followed by "DAGENS RÄTT … VEGETARISKT …" are supported.
- **Image** is read with OCR (Tesseract, Swedish model). The model (~20 MB) is downloaded on first use to `.storage/schoolsoft_plus_tessdata`.
- If OCR gets it wrong, you can pick an **AI Task** entity (`ai_task`) under *Configure* to read the image instead.

Parsed menus are cached in `.storage`, so the same file is only parsed once.

## Installation

### HACS (custom repository)
1. HACS → ⋮ → *Custom repositories* → add `https://github.com/petterbellander/ha-schoolsoft-plus`, type *Integration*.
2. Install **SchoolSoft Plus** and restart Home Assistant.

### Manual
Copy `custom_components/schoolsoft_plus` to `config/custom_components/` and restart.

### Configuration
*Settings → Devices & services → Add integration → SchoolSoft Plus.*

- **School**: the part of the login URL after `sms.schoolsoft.se/`, e.g. `myschool` for `https://sms.schoolsoft.se/myschool/`.
- **Username / password**: the guardian's SchoolSoft login (not BankID).

Don't have a username and password? See [Getting a SchoolSoft username and password](docs/login.md). You can request them yourself from your school's login page.

## Limitations

- Only guardian login with username and password. BankID is not supported.
- SchoolSoft has no public API. The integration reads the same pages as the web interface and may break if SchoolSoft changes them.
- Tested against one school. Other schools may be set up differently; please open an issue with (anonymised) examples if something isn't parsed correctly.
- OCR requires the `tesserocr` package. It works on Home Assistant OS; on other installation types the Tesseract libraries may need to be installed separately.

## AI assistance

This integration was developed with an AI assistant ([Claude Code](https://claude.com/claude-code)): the code, the documentation and the dashboard examples were written by the assistant under my direction. I run it daily on my own Home Assistant installation and review the changes, but please keep this in mind and report anything that looks wrong.

## Licence

MIT, see [LICENSE](LICENSE). The code in `vendor/` comes from schoolsoft-mcp and has its own MIT licence.
