# SchoolSoft Plus för Home Assistant

En anpassad integration som hämtar information från [SchoolSoft](https://www.schoolsoft.se/) med ett vårdnadshavarkonto: schema, läxor, prov, frånvaro, nyheter och skollunch, för alla barn som är kopplade till kontot.

*A Home Assistant custom integration for the Swedish school platform SchoolSoft (guardian accounts). Documentation is in Swedish since SchoolSoft is only used in Sweden.*

## Bygger på kanylbullens projekt

Integrationen bygger på två projekt av [kanylbullen](https://github.com/kanylbullen):

- **[ha-schoolsoft](https://github.com/kanylbullen/ha-schoolsoft)**, den ursprungliga Home Assistant-integrationen för SchoolSoft (lunch). Den var förebilden för den här integrationen.
- **[schoolsoft-mcp](https://github.com/kanylbullen/schoolsoft-mcp)**: klienten och tolkarna för SchoolSofts sidor ingår i `custom_components/schoolsoft_plus/vendor/` (MIT-licens, se [vendor/LICENSE](custom_components/schoolsoft_plus/vendor/LICENSE)). Klienten är ändrad så att den använder Home Assistants HTTP-klient.

Tack till kanylbullen för grundarbetet. Domänen heter `schoolsoft_plus` så att den kan installeras bredvid ha-schoolsoft (`schoolsoft`) utan att krocka.

## Vad du får

Per barn:

| Typ | Entitet |
|---|---|
| Kalender | Schema, Läxor och uppgifter, Prov, Lunch |
| Sensor | Skolstart idag, Skolslut idag, Skolstart imorgon |
| Sensor | Nästa prov (kommande prov som attribut), Öppna uppgifter |
| Sensor | Ej anmäld frånvaro, Ämnen med varning |
| Sensor | Lunch idag (vegetariskt alternativ och morgondagens lunch som attribut) |
| Sensor | Senaste nyhet |
| Sensor | Fritids hämtning idag (bara om barnet går på fritids) |

Per skola (om skolan länkar en matsedel under "Matsedel" på sidan Skolinformation):

| Typ | Entitet |
|---|---|
| Sensor | Veckans matsedel (veckonummer, dagarna som attribut `dagar`) |
| Bild | Matsedel (när matsedeln är en bild) |

Schema och uppgifter uppdateras var 30:e minut. Lunch, bedömningar, frånvaro och nyheter var tredje timme.

## Matsedel som PDF eller bild

Vissa skolor har ingen lunch i SchoolSoft utan länkar en veckomatsedel, ofta en fil på Google Drive. Integrationen laddar ner den:

- **PDF** tolkas som text. Både enkla upplägg ("Måndag: Köttbullar … Vegetariskt: …") och upplägg med ett tema per dag följt av "DAGENS RÄTT … VEGETARISKT …" stöds.
- **Bild** läses med OCR (Tesseract, svensk modell). Modellen (~20 MB) laddas ner första gången till `.storage/schoolsoft_plus_tessdata`.
- Blir OCR fel kan du under *Konfigurera* välja en **AI-uppgift** (`ai_task`-entitet) som läser bilden i stället.

Tolkade matsedlar sparas i `.storage`, så samma fil tolkas bara en gång.

## Installation

### HACS (anpassat repo)
1. HACS → ⋮ → *Anpassade repon* → lägg till `https://github.com/petterbellander/ha-schoolsoft-plus`, typ *Integration*.
2. Installera **SchoolSoft Plus** och starta om Home Assistant.

### Manuellt
Kopiera `custom_components/schoolsoft_plus` till `config/custom_components/` och starta om.

### Konfiguration
*Inställningar → Enheter och tjänster → Lägg till integration → SchoolSoft Plus.*

- **Skola**: delen av inloggningsadressen efter `sms.schoolsoft.se/`, t.ex. `minskola` för `https://sms.schoolsoft.se/minskola/`.
- **Användarnamn / lösenord**: vårdnadshavarens SchoolSoft-inloggning (inte BankID).

## Begränsningar

- Bara inloggning med användarnamn och lösenord för vårdnadshavare. BankID stöds inte.
- SchoolSoft har inget öppet API. Integrationen läser samma sidor som webbgränssnittet, och kan sluta fungera om SchoolSoft ändrar dem.
- Testad mot en skola. Andra skolor kan ha andra inställningar; öppna gärna ett ärende med (anonymiserade) exempel om något inte tolkas rätt.
- OCR kräver paketet `tesserocr`. Det fungerar på Home Assistant OS; på andra installationer kan Tesseract-biblioteken behöva installeras separat.

## Licens

MIT, se [LICENSE](LICENSE). Koden i `vendor/` kommer från schoolsoft-mcp och har sin egen MIT-licens.
