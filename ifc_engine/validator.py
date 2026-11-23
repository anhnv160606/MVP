"""IFC schema validation is separate from placement/design validation."""

import ifcopenshell
import ifcopenshell.validate


def validate_model(path, *, express_rules: bool = False) -> dict:
    """Return schema diagnostics without altering the input IFC or approving design."""
    model = ifcopenshell.open(str(path))
    logger = ifcopenshell.validate.json_logger()
    ifcopenshell.validate.validate(model, logger, express_rules=express_rules)
    return {
        "valid": not logger.statements,
        "issues": logger.statements,
        "scope": "IFC schema; does not establish electrical compliance",
    }
