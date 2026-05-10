from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import xml.etree.ElementTree as ET

from .classifier import DIR_TO_TYPE
from .io_utils import normalize_rel
from .xml_utils import child_text, local_name, parse_xml


@dataclass(frozen=True, slots=True)
class ObjectRef:
    rel_path: str
    abs_path: Path
    metadata_type: str
    xml_name: str
    uuid: str | None
    object_belonging: str | None
    extended_configuration_object: str | None
    parent_path: str | None
    logical_full_name: str

    @property
    def is_adopted(self) -> bool:
        return self.object_belonging == "Adopted"

    @property
    def is_extension_native(self) -> bool:
        return not self.is_adopted


@dataclass(slots=True)
class ObjectRegistry:
    root: Path
    by_rel: dict[str, ObjectRef]
    by_type_name: dict[tuple[str, str], ObjectRef]
    by_full_name: dict[str, ObjectRef]

    def find(self, metadata_type: str, name: str) -> ObjectRef | None:
        return self.by_type_name.get((metadata_type, name))


def metadata_root_object(tree: ET.ElementTree) -> ET.Element:
    root = tree.getroot()
    for item in list(root):
        if isinstance(item.tag, str):
            return item
    raise ValueError("MetaDataObject does not contain metadata object")


def _xml_object_name(path: Path) -> str | None:
    try:
        return child_text(metadata_root_object(parse_xml(path)), ["Properties", "Name"])
    except Exception:
        return None


def read_object_ref(root: Path, path: Path) -> ObjectRef | None:
    rel_path = normalize_rel(path.relative_to(root))
    parts = rel_path.split("/")
    if len(parts) < 2 or not rel_path.lower().endswith(".xml"):
        return None
    if parts[0] not in DIR_TO_TYPE:
        return None
    try:
        tree = parse_xml(path)
        obj = metadata_root_object(tree)
    except Exception:
        return None

    metadata_type = local_name(obj.tag)
    xml_name = child_text(obj, ["Properties", "Name"])
    if not xml_name:
        return None
    uuid = obj.attrib.get("uuid")
    object_belonging = child_text(obj, ["Properties", "ObjectBelonging"])
    extended = child_text(obj, ["Properties", "ExtendedConfigurationObject"])

    if len(parts) == 2:
        parent_path = None
        logical_full_name = f"{metadata_type}.{xml_name}"
    elif "/Forms/" in rel_path:
        owner_type = DIR_TO_TYPE.get(parts[0], parts[0])
        owner_name = _xml_object_name(root / parts[0] / f"{parts[1]}.xml") or parts[1]
        parent_path = "/".join(parts[:2])
        logical_full_name = f"{owner_type}.{owner_name}.Form.{xml_name}"
    else:
        owner_type = DIR_TO_TYPE.get(parts[0], parts[0])
        owner_name = _xml_object_name(root / parts[0] / f"{parts[1]}.xml") or parts[1]
        parent_path = "/".join(parts[:2])
        logical_full_name = f"{owner_type}.{owner_name}.{metadata_type}.{xml_name}"

    return ObjectRef(
        rel_path=rel_path,
        abs_path=path,
        metadata_type=metadata_type,
        xml_name=xml_name,
        uuid=uuid,
        object_belonging=object_belonging,
        extended_configuration_object=extended,
        parent_path=parent_path,
        logical_full_name=logical_full_name,
    )


def build_object_registry(root: Path) -> ObjectRegistry:
    by_rel: dict[str, ObjectRef] = {}
    by_type_name: dict[tuple[str, str], ObjectRef] = {}
    by_full_name: dict[str, ObjectRef] = {}
    if not root.exists():
        return ObjectRegistry(root=root, by_rel=by_rel, by_type_name=by_type_name, by_full_name=by_full_name)
    for path in sorted(root.rglob("*.xml"), key=lambda p: normalize_rel(p.relative_to(root)).lower()):
        ref = read_object_ref(root, path)
        if ref is None:
            continue
        by_rel[ref.rel_path] = ref
        by_type_name.setdefault((ref.metadata_type, ref.xml_name), ref)
        by_full_name.setdefault(ref.logical_full_name, ref)
    return ObjectRegistry(root=root, by_rel=by_rel, by_type_name=by_type_name, by_full_name=by_full_name)
