"""Abbreviations defined in the document text (Schwartz & Hearst, 2003)."""
from __future__ import annotations

from eval.abbrev import expand_mention, find_abbreviations

DOC = ("Pseudomonas aeruginosa (Pa) infection in cystic fibrosis (CF) patients is associated "
       "with worse disease, and chronic Pa infection (CPA) is associated with reduced lung function.")


def test_finds_long_form_before_parenthesis():
    ab = find_abbreviations(DOC)
    assert ab["Pa"] == "Pseudomonas aeruginosa"
    assert ab["CF"] == "cystic fibrosis"
    assert ab["CPA"] == "chronic Pa infection"


def test_first_letter_must_start_a_word():
    # 'NO' must not match the 'no' inside 'nitric oxide synthase' backwards from the wrong word
    ab = find_abbreviations("We measured nitric oxide (NO) levels.")
    assert ab["NO"] == "nitric oxide"


def test_rejects_parentheticals_that_are_not_abbreviations():
    ab = find_abbreviations("Doses were high (n = 12) and patients (aged 40 years) were treated (Table 1).")
    assert ab == {}


def test_short_form_first_when_parenthesis_holds_many_words():
    ab = find_abbreviations("Patients received 5-FU (5-fluorouracil) daily.")
    assert ab["5-FU"] == "5-fluorouracil"


def test_first_definition_wins():
    ab = find_abbreviations("acute renal failure (ARF) ... acute respiratory failure (ARF)")
    assert ab["ARF"] == "acute renal failure"


def test_expand_whole_token_and_nested_abbreviation():
    ab = find_abbreviations(DOC)
    assert expand_mention("CF", ab) == "cystic fibrosis"
    assert expand_mention("CPA", ab) == "chronic Pseudomonas aeruginosa infection"
    assert expand_mention("chronic Pa infection", ab) == "chronic Pseudomonas aeruginosa infection"
    assert expand_mention("CFTR", ab) is None          # no whole-token match


def test_expand_drops_the_definition_parenthesis_inside_a_mention():
    ab = find_abbreviations(DOC)
    assert expand_mention("Pseudomonas aeruginosa (Pa) infection", ab) == "Pseudomonas aeruginosa infection"


def test_expand_is_case_sensitive():
    assert expand_mention("no change", {"NO": "nitric oxide"}) is None


def test_ordinary_words_in_parentheses_are_not_short_forms():
    ab = find_abbreviations("mycophenolate mofetil and sirolimus (rapamycin) were given; "
                            "the maximum tolerated dose of paclitaxel (Taxol) was reached.")
    assert ab == {}
    assert find_abbreviations("amphotericin B (AmB) and allogeneic stem cell transplant (allo-SCT)") == {
        "AmB": "amphotericin B", "allo-SCT": "allogeneic stem cell transplant"}
