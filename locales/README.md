# Adding an interface language

English (`en.json`) is the source catalog. Ukrainian (`uk.json`) is included.

1. Copy `en.json` to a language-code filename such as `de.json`.
2. Change `language` to the filename stem (`de`) and `name` to the native language name (`Deutsch`).
3. Translate the values in `messages`. Keep the English keys unchanged. Empty or missing values fall back to English.
4. Preserve placeholders exactly, including `{count}`, `{text}`, `{device}`, `{index}`, and `{total}`. Preserve intentional line breaks. Do not translate technical identifiers inside values, such as `twitch-piper`.
5. Restart the app. The catalog is discovered automatically under Preferences → Appearance → Interface language. Select it, save, and restart again to apply it.

UI text is translated when widgets are created and when statuses update. `LocalizedCombo` stores stable English identifiers as item data, keeping preferences and engine/device selection independent of translated labels. Chat content, voice preset names, paths, tokens, and user-defined replacements are not translated.

When adding interface text, pass it through the window's `translate` callable and add an entry to both source and translated catalogs. Dynamic status messages use named placeholders. Sections separated by ` · ` can be translated independently. Avoid broad templates that would match arbitrary user text.

Validate JSON and run the localization tests:

```bash
python3 -m json.tool locales/de.json > /dev/null
QT_QPA_PLATFORM=offscreen python3 -m unittest -v test_localization
```

No placeholder catalogs for untranslated languages are shipped, so the selector only offers languages with an installed catalog.
