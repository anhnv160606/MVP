import pytest

from tools.ifcmcp_read import IfcMCPReader
from tools.registry import ToolRegistry


@pytest.fixture
def mcp_reader(model_file):
    pytest.importorskip("ifcmcp.core")
    return IfcMCPReader(model_file.path)


@pytest.mark.parametrize(
    "name,arguments",
    [
        ("ifc_summary", {}),
        ("ifc_tree", {}),
        ("ifc_select", {"query": "IfcSpace"}),
        ("ifc_contexts", {}),
        ("ifc_materials", {}),
        ("ifc_schema", {"entity_type": "IfcOutlet"}),
        ("ifc_validate", {"express_rules": False}),
    ],
)
def test_ifcmcp_read_operations(mcp_reader, name, arguments):
    result = mcp_reader.call(name, **arguments)
    assert isinstance(result, (dict, list))
    registry = ToolRegistry()
    registry.add(
        getattr(mcp_reader.session, name),
        description="Read-only query on this project's pinned IFC model. " + name,
    )
    assert registry.execute(name, arguments) == result


def test_ifcmcp_element_inspection_and_isolation(mcp_reader, model_file):
    step = model_file.model.by_guid(model_file.room).id()
    assert isinstance(mcp_reader.call("ifc_info", element_id=step), dict)
    assert isinstance(mcp_reader.call("ifc_relations", element_id=step), (dict, list))
    assert isinstance(mcp_reader.call("ifc_clash", element_id=step), dict)
    with pytest.raises(ValueError):
        mcp_reader.call("ifc_edit", function_path="root.remove_product")
