import gzip
from pathlib import Path

from eltool.mesh import Descriptor, read_descriptors

FIX = Path(__file__).parent / "fixtures" / "mesh_mini.xml"


def test_reads_only_the_branch():
    assert [d.ui for d in read_descriptors(FIX)] == ["D012140", "D001982", "D001249"]


def test_descriptor_fields():
    asthma = {d.ui: d for d in read_descriptors(FIX)}["D001249"]
    assert asthma.name == "Asthma"
    assert asthma.terms == ("Asthma", "Bronchial Asthma")
    assert asthma.tree_numbers == ("C08.127.108",)
    assert asthma.definition == asthma.definition.strip() != ""
    assert asthma.see_also == ("D001982",)


def test_reads_gzip(tmp_path):
    gz = tmp_path / "d.gz"
    gz.write_bytes(gzip.compress(FIX.read_bytes()))
    assert len(list(read_descriptors(gz))) == 3


def test_other_branch():
    assert [d.ui for d in read_descriptors(FIX, branch="D")] == ["D000001"]


def one_record(tmp_path, body):
    path = tmp_path / "one.xml"
    path.write_text(
        "<DescriptorRecordSet><DescriptorRecord>"
        "<DescriptorUI>D1</DescriptorUI>"
        "<DescriptorName><String>Name</String></DescriptorName>"
        f"{body}"
        "</DescriptorRecord></DescriptorRecordSet>",
        encoding="utf-8",
    )
    return list(read_descriptors(path))


def test_optional_elements_may_be_missing(tmp_path):
    body = "<TreeNumberList><TreeNumber>C01</TreeNumber></TreeNumberList>"
    assert one_record(tmp_path, body) == [
        Descriptor("D1", "Name", ("Name",), ("C01",), "", ())
    ]


def test_record_without_tree_numbers_is_skipped(tmp_path):
    assert one_record(tmp_path, "") == []


def test_any_tree_number_selects_and_all_are_kept(tmp_path):
    body = (
        "<TreeNumberList><TreeNumber>A01.1</TreeNumber>"
        "<TreeNumber>C02.2</TreeNumber></TreeNumberList>"
    )
    [d] = one_record(tmp_path, body)
    assert d.tree_numbers == ("A01.1", "C02.2")


def test_name_leads_the_terms(tmp_path):
    body = (
        "<TreeNumberList><TreeNumber>C01</TreeNumber></TreeNumberList>"
        "<ConceptList><Concept PreferredConceptYN='Y'><TermList>"
        "<Term><String>Other</String></Term><Term><String>NAME</String></Term>"
        "</TermList></Concept></ConceptList>"
    )
    [d] = one_record(tmp_path, body)
    assert d.terms == ("Name", "Other")


def test_definition_comes_only_from_the_preferred_concept(tmp_path):
    def body(preferred_note):
        return (
            "<TreeNumberList><TreeNumber>C01</TreeNumber></TreeNumberList>"
            "<ConceptList>"
            "<Concept PreferredConceptYN='N'><ScopeNote>narrower</ScopeNote></Concept>"
            f"<Concept PreferredConceptYN='Y'>{preferred_note}</Concept>"
            "</ConceptList>"
        )

    for note, expected in [
        ("<ScopeNote> main </ScopeNote>", "main"),
        ("<ScopeNote/>", ""),
        ("<ScopeNote>  </ScopeNote>", ""),
        ("", ""),
    ]:
        [d] = one_record(tmp_path, body(note))
        assert d.definition == expected, note
