# Dashboard card examples

Ready-to-use cards for the attributes of SchoolSoft Plus. All examples use built-in cards only (no custom cards needed). Paste them in the dashboard editor via *Add card → Manual*.

Replace the entity IDs with your own:

- `anna` → your child's name as it appears in the entity IDs (e.g. `sensor.schoolsoft_anna_nasta_prov`)
- `minskola` → your school slug (e.g. `sensor.schoolsoft_minskola_veckans_matsedel`)

The card texts are in Swedish to match the data from SchoolSoft; change them as you like.

## Today's / tomorrow's lessons

Shows today's lessons until school ends, then switches to tomorrow.

```yaml
type: markdown
title: Schema – Anna
content: |
  {%- set idag = 'sensor.schoolsoft_anna_skolstart_idag' -%}
  {%- set imorgon = 'sensor.schoolsoft_anna_skolstart_imorgon' -%}
  {%- set slut = states('sensor.schoolsoft_anna_skolslut_idag') -%}
  {%- set efter_skolan = slut in ['unknown', 'unavailable'] or now() > as_datetime(slut) -%}
  {%- set e = imorgon if efter_skolan else idag -%}
  **{{ 'Imorgon' if efter_skolan else 'Idag' }}**
  {%- set lektioner = state_attr(e, 'lektioner') or [] %}
  {% if lektioner %}
  {%- for l in lektioner %}
  - {{ l }}
  {%- endfor %}
  {%- else %}
  Ingen skola 🎉
  {%- endif %}
```

## Upcoming tests

Uses the `upcoming` attribute of *Next test*.

```yaml
type: markdown
title: Prov – Anna
content: |
  {%- set prov = state_attr('sensor.schoolsoft_anna_nasta_prov', 'upcoming') or [] -%}
  {%- set dagar = ['mån','tis','ons','tors','fre','lör','sön'] -%}
  {%- if prov | count == 0 -%}
  Inga kommande prov 🎉
  {%- else -%}
  {%- for p in prov -%}
  {%- set d = as_datetime(p.datum).date() -%}
  {%- set kvar = (d - now().date()).days %}
  **{{ dagar[d.weekday()] }} {{ d.day }}/{{ d.month }}** · {{ p.amne }} · {{ 'idag' if kvar == 0 else 'imorgon' if kvar == 1 else kvar ~ ' dagar' }}
  {{ p.titel }}
  {% endfor -%}
  {%- endif -%}
```

## Open assignments and homework

Uses the `items` attribute of *Open assignments*, sorted by deadline.

```yaml
type: markdown
title: Uppgifter – Anna
content: |
  {%- set items = state_attr('sensor.schoolsoft_anna_oppna_uppgifter', 'items') or [] -%}
  {%- if items | count == 0 -%}
  Inga öppna uppgifter ✅
  {%- else -%}
  {%- for u in items | sort(attribute='deadline') -%}
  {%- set kvar = (as_datetime(u.deadline).date() - now().date()).days %}
  **{{ u.amne }}** · {{ u.titel }}
  <small>{{ u.typ }} · {{ 'idag' if kvar == 0 else 'imorgon' if kvar == 1 else 'om ' ~ kvar ~ ' dagar' if kvar > 1 else 'försenad' }}</small>
  {% endfor -%}
  {%- endif -%}
```

## Lunch for the rest of the week

Uses the `dagar` attribute of *Weekly menu* (schools that link a menu file). Set `visa_veg` to `false` to hide the vegetarian option. Falls back to a link to the menu file if it couldn't be parsed.

```yaml
type: markdown
title: Lunch
content: |
  {%- set e = 'sensor.schoolsoft_minskola_veckans_matsedel' -%}
  {%- set visa_veg = true -%}
  {%- set dagar = state_attr(e, 'dagar') or [] -%}
  {%- set idag = now().date() | string -%}
  {%- set kommande = dagar | selectattr('datum', '>=', idag) | list -%}
  {%- if kommande | count > 0 -%}
  {%- for d in kommande %}
  **{{ 'Idag' if d.datum == idag else d.dag }}** · {{ d.ratt }}
  {%- if visa_veg and d.vegetariskt %}
  <small>🌱 {{ d.vegetariskt }}</small>
  {%- endif %}
  {% endfor -%}
  {%- elif states(e) not in ['unknown', 'unavailable'] -%}
  Matsedel {{ states(e) }} – [öppna filen]({{ state_attr(e, 'lank') }})
  {%- else -%}
  Ingen matsedel hittad
  {%- endif -%}
```

For schools with lunch in SchoolSoft itself, use *Lunch today* instead: the state is today's dish, with `vegetariskt`, `imorgon` and `imorgon_vegetariskt` as attributes.

```yaml
type: tile
entity: sensor.schoolsoft_anna_lunch_idag
name: Lunch idag
vertical: false
```

## Only show when something needs attention

Unreported absence and subjects with warnings, hidden when there is nothing to act on.

```yaml
type: vertical-stack
cards:
  - type: conditional
    conditions:
      - condition: numeric_state
        entity: sensor.schoolsoft_anna_ej_anmald_franvaro
        above: 0
    card:
      type: tile
      entity: sensor.schoolsoft_anna_ej_anmald_franvaro
      name: Ej anmäld frånvaro
      color: red
  - type: conditional
    conditions:
      - condition: numeric_state
        entity: sensor.schoolsoft_anna_amnen_med_varning
        above: 0
    card:
      type: tile
      entity: sensor.schoolsoft_anna_amnen_med_varning
      name: Ämnen med varning
      color: orange
```

## Calendars

The schedule, assignment, test and lunch calendars work with the built-in calendar card, or any custom calendar card.

```yaml
type: calendar
initial_view: listWeek
entities:
  - calendar.schoolsoft_anna_schema
  - calendar.schoolsoft_anna_prov
```
