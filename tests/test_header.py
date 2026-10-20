import unittest

from lib import header

GOOD = "# A title\n\nid: one\nname: two words\n"


class ParseTests(unittest.TestCase):
    def test_good_header_parses(self):
        title, pairs, problems = header.parse(GOOD)
        self.assertEqual(title, "A title")
        self.assertEqual(pairs, [("id", "one"), ("name", "two words")])
        self.assertEqual(problems, [])

    def test_block_ends_at_first_blank_line(self):
        title, pairs, problems = header.parse(GOOD + "\nbody text: ignored\n")
        self.assertEqual(pairs, [("id", "one"), ("name", "two words")])
        self.assertEqual(problems, [])

    def test_title_line_must_be_a_heading(self):
        _t, _p, problems = header.parse("A title\n\nid: one\n")
        self.assertEqual(problems[0][0], 1)

    def test_title_must_not_be_empty(self):
        title, _p, problems = header.parse("# \n\nid: one\n")
        self.assertIsNone(title)
        self.assertEqual(problems[0][0], 1)

    def test_line_two_must_be_blank(self):
        _t, _p, problems = header.parse("# T\nid: one\n")
        self.assertIn(2, [n for n, _ in problems])
        _t, _p, ok = header.parse("# T\n\nid: one\n")
        self.assertEqual(ok, [])

    def test_block_needs_at_least_one_line(self):
        _t, pairs, problems = header.parse("# T\n\n")
        self.assertEqual(pairs, [])
        self.assertEqual(len(problems), 1)

    def test_key_shape(self):
        _t, pairs, problems = header.parse("# T\n\nBad: one\n")
        self.assertEqual(pairs, [])
        self.assertEqual(problems[0][0], 3)
        _t, pairs, problems = header.parse("# T\n\nok_key2: one\n")
        self.assertEqual(pairs, [("ok_key2", "one")])
        self.assertEqual(problems, [])

    def test_keys_are_unique(self):
        _t, pairs, problems = header.parse("# T\n\nid: one\nid: two\n")
        self.assertEqual(pairs, [("id", "one")])
        self.assertEqual(problems[0][0], 4)

    def test_value_is_never_empty(self):
        _t, _p, problems = header.parse("# T\n\nid:    \n")
        self.assertEqual(len(problems), 1)
        _t, pairs, problems = header.parse("# T\n\nid: -\n")
        self.assertEqual(pairs, [("id", "-")])
        self.assertEqual(problems, [])

    def test_value_never_holds_a_pipe(self):
        _t, pairs, problems = header.parse("# T\n\nid: a | b\n")
        self.assertEqual(pairs, [])
        self.assertEqual(len(problems), 1)

    def test_values_are_stripped(self):
        _t, pairs, _p = header.parse("# T\n\nid:   padded   \n")
        self.assertEqual(pairs, [("id", "padded")])

    def test_other_lines_inside_the_block_are_problems(self):
        _t, pairs, problems = header.parse("# T\n\nid: one\nnot a pair\n")
        self.assertEqual(pairs, [("id", "one")])
        self.assertEqual(problems[0][0], 4)

    def test_source_keys_are_in_order(self):
        self.assertEqual(header.SOURCE_KEYS[0], "id")
        self.assertEqual(header.SOURCE_KEYS[-1], "notes")
        self.assertEqual(len(header.SOURCE_KEYS), 10)


if __name__ == "__main__":
    unittest.main()
