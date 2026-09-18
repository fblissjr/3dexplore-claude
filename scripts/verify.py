#!/usr/bin/env python3
"""Preflight a native 3dexplore-claude 3MF directly; return nonzero on invalid geometry."""
import argparse
import json
from x2d.validation import inspect_3mf


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("project")
    args = parser.parse_args()
    try:
        report = inspect_3mf(args.project)
        try:
            import lib3mf
        except ImportError:
            report['lib3mf'] = {'skipped': 'lib3mf not installed'}
        else:
            wrapper = lib3mf.get_wrapper()
            model = wrapper.CreateModel()
            reader = model.QueryReader('3mf')
            reader.ReadFromFile(args.project)
            iterator = model.GetObjects()
            solids = []
            while iterator.MoveNext():
                obj = iterator.GetCurrentObject()
                if obj.IsMeshObject():
                    solids.append(bool(obj.IsManifoldAndOriented()))
            report['lib3mf'] = {
                'manifold_and_oriented': all(solids),
                'warnings': [reader.GetWarning(i) for i in range(reader.GetWarningCount())],
            }
            if not solids or not all(solids):
                report['valid'] = False
                report['errors'].append('lib3mf rejected mesh topology')
    except Exception as exc:
        report = {"valid": False, "errors": [str(exc)]}
    print(json.dumps(report, indent=2))
    return 0 if report["valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
