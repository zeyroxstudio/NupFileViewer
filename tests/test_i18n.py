import ast
from pathlib import Path
from string import Formatter
import unittest

from nupviewer import i18n


class LanguageTests(unittest.TestCase):
    def test_supported_languages_and_fallback(self):
        self.addCleanup(i18n.set_language, "en")
        expected = {"en": "Overview", "it": "Riepilogo", "es": "Resumen", "fr": "Aperçu"}
        self.assertEqual(set(i18n.LANGUAGES), set(expected))
        for code, value in expected.items():
            i18n.set_language(code)
            self.assertEqual(i18n.tr("Overview"), value)
        i18n.set_language("unsupported")
        self.assertEqual(i18n.tr("Overview"), "Overview")

    def test_catalog_covers_source_strings_and_preserves_placeholders(self):
        package = Path(i18n.__file__).parent
        for name in ("ui.py", "archive.py", "updates.py"):
            tree = ast.parse((package / name).read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                        and node.func.id == "tr" and isinstance(node.args[0], ast.Constant)):
                    self.assertIn(node.args[0].value, i18n.MESSAGES)

        def fields(text):
            return sorted((name, spec, conversion) for _, name, spec, conversion in Formatter().parse(text)
                          if name is not None)

        for english, translations in i18n.MESSAGES.items():
            with self.subTest(message=english):
                self.assertEqual(set(translations), {"it", "es", "fr"})
                for text in translations.values():
                    self.assertEqual(fields(text), fields(english))
