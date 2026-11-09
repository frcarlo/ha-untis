# Untis für Home Assistant

Stundenplan aus WebUntis in Home Assistant: Kalender, Sensoren für aktuelle und nächste Stunde, Schulbeginn/-ende und Ausfälle. Dazu ein Event bei neuen Ausfällen und Vertretungen. Funktioniert mit Eltern-Konten (alle Kinder) und Schüler-Konten.

Portiert aus dem WebUntis-Widget von [frcarlo/landing-page](https://github.com/frcarlo/landing-page), einige Ideen stammen aus [JonasJoKuJonas/homeassistant-WebUntis](https://github.com/JonasJoKuJonas/homeassistant-WebUntis). Keine externen Python-Abhängigkeiten.

## Installation

HACS → Benutzerdefinierte Repositories → `https://github.com/frcarlo/ha-untis` (Typ: Integration), oder den Ordner `custom_components/untis` nach `/config/custom_components/` kopieren. Danach Home Assistant neu starten und unter *Einstellungen → Geräte & Dienste* „Untis“ hinzufügen.

- **URL**: die Adresse aus dem Browser inklusive Schule, z. B. `https://xyz.webuntis.com/WebUntis/?school=name`
- **Benutzername / Passwort**: Eltern- oder Schülerkonto

Bei Eltern-Konten mit mehreren Kindern wählst du danach aus, welche Kinder eingerichtet werden. Das lässt sich später über *Konfigurieren* ändern.

## Entitäten (pro Kind ein Gerät)

| Entität | Inhalt |
| --- | --- |
| `calendar.<kind>_stundenplan` | Stunden ab heute für 14 Tage. Ausfälle mit ❌, Änderungen mit ⚠️ |
| `sensor.<kind>_aktuelle_stunde` | Fach der laufenden Stunde, Attribute: Raum, Lehrer, Zeiten, Status |
| `sensor.<kind>_nachste_stunde` | Fach der nächsten Stunde (ohne Ausfälle) |
| `sensor.<kind>_beginn_nachste_stunde` | Zeitstempel der nächsten Stunde |
| `sensor.<kind>_nachste_stunde_zum_aufstehen` | Erste Stunde des nächsten Tages, an dem die Schule noch nicht begonnen hat (für Wecker) |
| `sensor.<kind>_schulbeginn_heute` / `_schulende_heute` | Zeitstempel, leer an schulfreien Tagen |
| `sensor.<kind>_schulbeginn_morgen` / `_schulende_morgen` | dito für morgen |
| `sensor.<kind>_ausfalle_heute` / `_morgen` | Anzahl entfallener Stunden, Attribut `lessons` |
| `sensor.<kind>_anderungen_heute` / `_morgen` | Anzahl geänderter Stunden (Vertretung, Raum), Attribut `lessons` |
| `event.<kind>_stundenanderung` | Feuert bei neuen Ausfällen/Änderungen für heute und morgen |

Aktualisierung alle 15 Minuten. Pro Abruf gibt es einen Login für alle Kinder und danach einen Logout.

## Benachrichtigung bei Ausfall

Das Event liefert eine fertige Zeile in `message`, z. B. `08:00 Mathematik entfällt` oder `09:45 Deutsch geändert (Raum B12)`.

```yaml
triggers:
  - trigger: state
    entity_id: event.max_stundenanderung
    not_from: unavailable
actions:
  - action: notify.mobile_app_handy
    data:
      title: "Max – {{ 'heute' if trigger.to_state.attributes.day == 'today' else 'morgen' }}"
      message: "{{ trigger.to_state.attributes.message }}"
```

Alternativ gibt es das Bus-Event `untis_lesson_changed` mit denselben Daten plus `student` und `student_id`.

## Service `untis.get_timetable`

Liefert die Stunden eines Kindes für einen beliebigen Zeitraum (bis 62 Tage):

```yaml
action: untis.get_timetable
data:
  device_id: <Gerät des Kindes>
  start: "2026-11-16"
  end: "2026-11-20"
  show_cancelled: false
response_variable: plan
```

## Grenzen

- Hausaufgaben und Prüfungen sind (noch) nicht drin. WebUntis liefert sie bei Eltern-Konten ohnehin nicht.
- Konten mit Zwei-Faktor-Anmeldung werden nicht unterstützt.
