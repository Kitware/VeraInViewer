import pytest

from vera_in_viewer.parser import convert, parse_file, parse_string


@pytest.mark.parametrize(
    ("type_name", "value", "expected"),
    [
        ("double", "1.5", 1.5),
        ("int", "3", 3),
        ("int", "3.0", 3),
        ("bool", "True", True),
        ("bool", "false", False),
        ("string", "abc", "abc"),
        ("Array(double)", "{1, 2.5,3}", [1.0, 2.5, 3.0]),
        ("Array(string)", "{a,-,b}", ["a", "-", "b"]),
        ("Array(int)", "{}", []),
    ],
)
def test_convert(type_name, value, expected):
    assert convert(type_name, value) == expected


def test_parse_nested_lists():
    data = parse_string(
        """<ParameterList name="CASEID">
             <Parameter name="case_id" type="string" value="demo"/>
             <ParameterList name="CORE"><Parameter name="core_size" type="int" value="2"/></ParameterList>
           </ParameterList>"""
    )
    assert data == {"CASEID": {"case_id": "demo", "CORE": {"core_size": 2}}}


def test_parse_file(mini_core_path):
    caseid = parse_file(mini_core_path)["CASEID"]
    assert caseid["case_id"] == "Mini core"
    assert caseid["CORE"]["shape"] == [0, 1, 0, 1, 1, 1, 0, 1, 0]
    assert caseid["ASSEMBLIES"]["Assembly_1"]["Cells"]["Cell_1"]["mats"] == ["U31", "he", "zirc4"]
    assert caseid["STATES"]["State_1"]["feedback"] is True
