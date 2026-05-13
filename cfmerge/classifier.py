from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path
from pathlib import PurePosixPath


NS_MD = "http://v8.1c.ru/8.3/MDClasses"
NS_LF = "http://v8.1c.ru/8.3/xcf/logform"
NS_ROLES = "http://v8.1c.ru/8.2/roles"


def _local_name(tag: str) -> str:
    return tag.split("}", 1)[-1] if tag.startswith("{") else tag


def _namespace(tag: str) -> str:
    return tag[1:].split("}", 1)[0] if tag.startswith("{") else ""


def classify_path(rel_path: str) -> str:
    rel = rel_path.replace("\\", "/")
    name = PurePosixPath(rel).name
    if rel == "Configuration.xml":
        return "root_configuration"
    if rel == "ConfigDumpInfo.xml":
        return "config_dump_info"
    if name == "ОтчетПоКонфигурации.txt":
        return "configuration_report"
    if name.endswith(".bsl"):
        return "bsl_module"
    if rel.endswith("/Ext/Form.xml"):
        return "form_visual_xml"
    if rel.endswith("/Ext/CommandInterface.xml"):
        return "command_interface_xml"
    if rel.endswith("/Ext/Rights.xml"):
        return "rights_xml"
    if "/Ext/" in rel and name.endswith(".xml"):
        return "unknown_xml"
    if "/Forms/" in rel and name.endswith(".xml"):
        return "form_object_xml"
    if name.endswith(".xml"):
        return "metadata_xml"
    return "binary_or_resource"


def classify_xml_root(root: ET.Element, coarse: str) -> str:
    root_local = _local_name(root.tag)
    root_ns = _namespace(root.tag)
    if root_local == "MetaDataObject" and root_ns == NS_MD:
        object_element = next((item for item in list(root) if isinstance(item.tag, str)), None)
        if object_element is not None:
            return coarse if coarse in {"metadata_xml", "form_object_xml"} else "metadata_xml"
        return "unknown_xml"
    if root_local == "Form" and root_ns == NS_LF:
        return "form_visual_xml"
    if root_local == "CommandInterface":
        return "command_interface_xml"
    if root_local == "Rights" and root_ns == NS_ROLES:
        return "rights_xml"
    return "unknown_xml"


def classify_file(path: Path, rel_path: str) -> str:
    coarse = classify_path(rel_path)
    if not rel_path.lower().endswith(".xml"):
        return coarse
    if coarse in {"root_configuration", "config_dump_info", "form_visual_xml", "rights_xml", "unknown_xml"}:
        return coarse

    try:
        root = ET.parse(path).getroot()
    except Exception:
        return "unknown_xml"

    return classify_xml_root(root, coarse)


DIR_TO_TYPE = {
    "Languages": "Language",
    "Subsystems": "Subsystem",
    "StyleItems": "StyleItem",
    "Styles": "Style",
    "CommonPictures": "CommonPicture",
    "SessionParameters": "SessionParameter",
    "Roles": "Role",
    "CommonTemplates": "CommonTemplate",
    "FilterCriteria": "FilterCriterion",
    "CommonModules": "CommonModule",
    "CommonAttributes": "CommonAttribute",
    "ExchangePlans": "ExchangePlan",
    "XDTOPackages": "XDTOPackage",
    "WebServices": "WebService",
    "HTTPServices": "HTTPService",
    "WSReferences": "WSReference",
    "EventSubscriptions": "EventSubscription",
    "ScheduledJobs": "ScheduledJob",
    "SettingsStorages": "SettingsStorage",
    "FunctionalOptions": "FunctionalOption",
    "FunctionalOptionsParameters": "FunctionalOptionsParameter",
    "DefinedTypes": "DefinedType",
    "Bots": "Bot",
    "CommonCommands": "CommonCommand",
    "CommandGroups": "CommandGroup",
    "Constants": "Constant",
    "CommonForms": "CommonForm",
    "Catalogs": "Catalog",
    "Documents": "Document",
    "DocumentNumerators": "DocumentNumerator",
    "Sequences": "Sequence",
    "DocumentJournals": "DocumentJournal",
    "Enums": "Enum",
    "Reports": "Report",
    "DataProcessors": "DataProcessor",
    "InformationRegisters": "InformationRegister",
    "AccumulationRegisters": "AccumulationRegister",
    "ChartsOfCharacteristicTypes": "ChartOfCharacteristicTypes",
    "ChartOfAccounts": "ChartOfAccounts",
    "AccountingRegisters": "AccountingRegister",
    "ChartsOfCalculationTypes": "ChartOfCalculationTypes",
    "CalculationRegisters": "CalculationRegister",
    "BusinessProcesses": "BusinessProcess",
    "Tasks": "Task",
    "IntegrationServices": "IntegrationService",
}

TYPE_TO_DIR = {v: k for k, v in DIR_TO_TYPE.items()}

CHILD_TYPE_ORDER = [
    "Language", "Subsystem", "StyleItem", "Style",
    "CommonPicture", "SessionParameter", "Role", "CommonTemplate",
    "FilterCriterion", "CommonModule", "CommonAttribute", "ExchangePlan",
    "XDTOPackage", "WebService", "HTTPService", "WSReference",
    "EventSubscription", "ScheduledJob", "SettingsStorage", "FunctionalOption",
    "FunctionalOptionsParameter", "DefinedType", "Bot", "CommonCommand", "CommandGroup",
    "Constant", "CommonForm", "Catalog", "Document",
    "DocumentNumerator", "Sequence", "DocumentJournal", "Enum",
    "Report", "DataProcessor", "InformationRegister", "AccumulationRegister",
    "ChartOfCharacteristicTypes", "ChartOfAccounts", "AccountingRegister",
    "ChartOfCalculationTypes", "CalculationRegister",
    "BusinessProcess", "Task", "IntegrationService",
]


def object_locator(rel_path: str) -> tuple[str | None, str | None]:
    rel = rel_path.replace("\\", "/")
    parts = rel.split("/")
    if len(parts) >= 2 and parts[0] in DIR_TO_TYPE:
        name = parts[1]
        if name.endswith(".xml"):
            name = name[:-4]
        return DIR_TO_TYPE[parts[0]], name
    return None, None
