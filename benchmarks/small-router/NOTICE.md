# Notice

`ha_intents.jsonl`, and the rows of `train.jsonl` whose `source` is `ha-intents`, contain
sentences from Home Assistant's intents repository, https://github.com/home-assistant/intents
(commit f073cc925fef0724834c9a4b5d0f135aa29dd9d8), licensed under Creative Commons Attribution
4.0 International (https://creativecommons.org/licenses/by/4.0/).

Changes: the sentences were mapped from Home Assistant's intents and slots onto this
benchmark's tools by `import_ha_intents.py`; list items that also appear in the test cases were
replaced with other items; some intents were dropped or capped.
