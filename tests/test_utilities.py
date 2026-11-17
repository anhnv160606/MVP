import xml.etree.ElementTree as ET

import pytest

from ifc_engine.merger import export_combined_model
from ifc_engine.validator import validate_model
from tools.routing import route_calculate_path
from tools.sld_generator import sld_generate


def test_reference_route_and_escaped_schedule():
    route = route_calculate_path([0, 0, 1], [3, 4, 1], 3)
    assert route["length_m"] == 11
    assert not route["validated"]
    preview = sld_generate("Panel <A>", [{"circuit_no": 1, "name": "Lights & plugs"}])
    ET.fromstring(preview["sld_svg"])
    assert "&lt;A&gt;" in preview["sld_svg"]
    with pytest.raises(ValueError):
        route_calculate_path([0, 0, 1], [3, 4, 1], float("nan"))
    with pytest.raises(ValueError):
        sld_generate("Panel", [{"circuit_no": 1}, {"circuit_no": 1}])


def test_schema_validation_and_export_protection(model_file, tmp_path):
    result = validate_model(model_file.path)
    assert isinstance(result["issues"], list)
    with pytest.raises(ValueError):
        export_combined_model(model_file.path, model_file.path, model_file.path)
    result = export_combined_model(model_file.path, model_file.path, tmp_path / "copy.ifc")
    assert result["status"] == "exported"
