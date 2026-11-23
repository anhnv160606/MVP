"""Schedule preview utility; excluded from the device-placement registry."""

from html import escape


def sld_generate(panel_name: str, circuits: list[dict]) -> dict:
    """Render supplied circuit labels as an SVG single-line schedule preview.

    Each circuit needs a unique circuit_no and name; optional breaker/rating
    values are displayed exactly as supplied. No sizing, topology inference or
    engineering approval is performed. SVG text is escaped for safe rendering.
    """
    if not isinstance(panel_name, str) or not panel_name.strip() or not isinstance(circuits, list):
        raise ValueError("Provide a panel name and circuit list")
    numbers = set()
    labels = []
    for circuit in circuits:
        number = circuit.get("circuit_no")
        if type(number) is not int or number < 1 or number in numbers:
            raise ValueError("circuit_no must be a unique positive integer")
        numbers.add(number)
        labels.append(escape(f"{number}: {circuit.get('name', '')} {circuit.get('breaker', '')}"))
    height = 100 + 40 * len(labels)
    svg = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="720" height="{height}" viewBox="0 0 720 {height}">',
        '<rect width="100%" height="100%" fill="white"/>',
        f'<text x="20" y="25" font-family="sans-serif">{escape(panel_name)} — PREVIEW</text>',
        f'<path d="M 40 45 V {height - 20}" stroke="black" fill="none"/>',
    ]
    for index, label in enumerate(labels):
        y = 65 + index * 40
        svg.extend(
            [
                f'<path d="M 40 {y} H 140" stroke="black"/>',
                f'<text x="150" y="{y + 5}" font-family="sans-serif">{label}</text>',
            ]
        )
    return {
        "sld_svg": "".join(svg) + "</svg>",
        "validated": False,
        "warnings": ["Preview of supplied schedule; electrical design has not been checked."],
    }
