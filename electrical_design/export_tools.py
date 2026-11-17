"""Export the same tool schemas used by the agent, without contacting Supabase."""

import argparse
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from agent.runtime import PlacementBackend
from config.settings import Settings
from memory.project import ProjectMemory


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ifc", type=Path, required=True)
    parser.add_argument("--ifcmcp", action="store_true")
    parser.add_argument("--output", type=Path, default=Path("docs/tool-schemas.json"))
    args = parser.parse_args(argv)
    with TemporaryDirectory(prefix="tool-schemas-") as directory:
        settings = Settings(
            _env_file=None, state_db=str(Path(directory) / "state.db"), enable_ifcmcp=args.ifcmcp
        )
        backend = PlacementBackend(settings, None, None, ProjectMemory(settings.state_db))
        registry = backend.registry(
            {
                "project_id": "schema-export",
                "ifc_path": str(args.ifc),
                "source_file_id": "schema-only",
                "plan": None,
            },
            {"questionnaire_revision": 1},
        )
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(registry.schemas(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        print(f"Exported {len(registry.tools)} tool schemas to {args.output}")


if __name__ == "__main__":
    main()
