from __future__ import annotations

from pathlib import PurePosixPath


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
    if "/Forms/" in rel and name.endswith(".xml"):
        return "form_object_xml"
    if name.endswith(".xml"):
        return "metadata_xml"
    return "binary_or_resource"


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
