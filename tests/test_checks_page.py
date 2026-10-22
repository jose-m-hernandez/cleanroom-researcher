import ast
import os
import re
import unittest

REAL_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PAGE = "docs/checks.md"
ADMISSION = "tools/check_admission.py"
VOCABULARY = "tools/check_vocabulary.py"
EXPERIMENTS = "tools/check_experiments.py"
TABLE_HEAD = "| Code | What it checks | Where |"
A_LITERAL = re.compile(r"^(A[0-9]+) ")
BULLET = re.compile(r"^- ([A-Z][0-9]+) ")

OPENING = (
    "This page says what each check code means. The words this repository uses on purpose, "
    "and the values each field may take, are in `docs/vocabulary.md`."
)
CLOSING = "E4 is reserved for a check not yet built. E5 is the `_raw` rule, which A10 implements."

EXPECTED = (
    ["A%d" % n for n in range(0, 11)]
    + ["V%d" % n for n in range(1, 8)]
    + ["E1", "E2", "E3", "E6", "E7", "S1", "S2", "S3"]
)


def read(relpath):
    with open(os.path.join(REAL_ROOT, *relpath.split("/")), encoding="utf-8") as handle:
        return handle.read()


def page_codes(text):
    """The first cell of each row of the page's table, in order."""
    codes = []
    inside = False
    for line in text.split("\n"):
        if line.strip() == TABLE_HEAD:
            inside = True
            continue
        if inside:
            if not line.startswith("|"):
                break
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            if set(cells[0]) <= set("-: "):
                continue
            codes.append(cells[0])
    return codes


def page_where(text):
    """Map each code in the page's table to its Where cell."""
    where = {}
    inside = False
    for line in text.split("\n"):
        if line.strip() == TABLE_HEAD:
            inside = True
            continue
        if inside:
            if not line.startswith("|"):
                break
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            if set(cells[0]) <= set("-: "):
                continue
            where[cells[0]] = cells[-1]
    return where


def admission_codes(source):
    """The string literals that start with A, digits and a space."""
    found = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            match = A_LITERAL.match(node.value)
            if match:
                found.add(match.group(1))
    return found


def docstring_codes(source, letters):
    """The `- X<n>` bullets of the module docstring whose letter is in letters."""
    doc = ast.get_docstring(ast.parse(source)) or ""
    found = set()
    for line in doc.split("\n"):
        match = BULLET.match(line)
        if match and match.group(1)[0] in letters:
            found.add(match.group(1))
    return found


def defined_codes(admission, vocabulary, experiments):
    return (
        admission_codes(admission)
        | docstring_codes(vocabulary, "V")
        | docstring_codes(experiments, "ES")
    )


class ChecksPageTests(unittest.TestCase):
    def setUp(self):
        self.page = read(PAGE)
        self.admission = read(ADMISSION)
        self.vocabulary = read(VOCABULARY)
        self.experiments = read(EXPERIMENTS)

    def defined(self):
        return defined_codes(self.admission, self.vocabulary, self.experiments)

    def test_the_code_defines_exactly_these_codes(self):
        self.assertEqual(len(EXPECTED), len(set(EXPECTED)))
        self.assertEqual(self.defined(), set(EXPECTED))

    def test_the_page_lists_each_code_once_in_order(self):
        self.assertEqual(page_codes(self.page), EXPECTED)

    def test_the_page_codes_equal_the_defined_codes(self):
        self.assertEqual(set(page_codes(self.page)), self.defined())

    def test_every_row_names_one_of_the_three_modules(self):
        for line in self.page.split("\n"):
            if line.startswith("| ") and line.strip() != TABLE_HEAD and not line.startswith("| ---"):
                self.assertTrue(
                    any(line.rstrip().endswith("`%s` |" % m) for m in (ADMISSION, VOCABULARY, EXPERIMENTS)),
                    line,
                )

    def test_each_where_cell_names_the_module_that_defines_the_code(self):
        owner = {}
        for code in admission_codes(self.admission):
            owner[code] = ADMISSION
        for code in docstring_codes(self.vocabulary, "V"):
            owner[code] = VOCABULARY
        for code in docstring_codes(self.experiments, "ES"):
            owner[code] = EXPERIMENTS
        where = page_where(self.page)
        self.assertEqual(set(where), set(owner))
        for code, module in owner.items():
            self.assertEqual(where[code], "`%s`" % module, code)

    def test_the_opening_paragraph_is_exact(self):
        self.assertEqual(self.page.split("\n")[2], OPENING)

    def test_the_closing_line_is_exact(self):
        self.assertEqual(self.page.rstrip("\n").split("\n")[-1], CLOSING)

    def test_a_fake_row_is_caught(self):
        fake = self.page.replace("| A0 |", "| Z9 | x | x |\n| A0 |", 1)
        self.assertNotEqual(fake, self.page)
        self.assertNotEqual(set(page_codes(fake)), self.defined())

    def test_a_deleted_row_is_caught(self):
        lines = [ln for ln in self.page.split("\n") if not ln.startswith("| A10 |")]
        self.assertEqual(len(lines), len(self.page.split("\n")) - 1)
        self.assertNotEqual(set(page_codes("\n".join(lines))), self.defined())

    def test_a_fake_vocabulary_bullet_is_caught(self):
        fake = self.vocabulary.replace("- V7 ", "- V8 a rule that is not built.\n- V7 ", 1)
        self.assertNotEqual(fake, self.vocabulary)
        derived = defined_codes(self.admission, fake, self.experiments)
        self.assertIn("V8", derived)
        self.assertNotEqual(set(page_codes(self.page)), derived)

    def test_a_fake_admission_literal_is_caught(self):
        fake = self.admission + '\nEXTRA = "A11 a rule that is not built"\n'
        derived = defined_codes(fake, self.vocabulary, self.experiments)
        self.assertIn("A11", derived)
        self.assertNotEqual(set(page_codes(self.page)), derived)


if __name__ == "__main__":
    unittest.main()
