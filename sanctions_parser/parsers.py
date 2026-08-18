from __future__ import annotations

import logging
import unicodedata
from collections.abc import Callable, Iterable
from pathlib import Path

from lxml import etree

from .models import (
    Address,
    Alias,
    Contact,
    DateOfBirth,
    Designation,
    Document,
    Entity,
    Nationality,
    NormalizedData,
    PlaceOfBirth,
    Program,
    Regulation,
    Sanction,
)

LOGGER = logging.getLogger("sanctions_parser.parser")
Element = etree._Element


def _local(element: Element) -> str:
    return etree.QName(element).localname


def _direct(element: Element, *names: str) -> Iterable[Element]:
    wanted = {name.lower() for name in names}
    return (child for child in element if _local(child).lower() in wanted)


def _descendants(element: Element, *names: str) -> Iterable[Element]:
    wanted = {name.lower() for name in names}
    return (
        child for child in element.iterdescendants() if _local(child).lower() in wanted
    )


def _node_text(element: Element) -> str:
    return " ".join("".join(element.itertext()).split())


def _text(element: Element, *names: str) -> str:
    for child in _direct(element, *names):
        value = _node_text(child)
        if value:
            return value
    return ""


def _attr(element: Element, *names: str) -> str:
    wanted = {name.lower() for name in names}
    for key, value in element.attrib.items():
        if etree.QName(key).localname.lower() in wanted and value:
            return value.strip()
    return ""


def _name(*parts: str) -> str:
    return " ".join(part.strip() for part in parts if part.strip())


def _positional_name_parts(*parts: str) -> tuple[str, str, str]:
    """Map ordered provider name slots without guessing from a full-name string."""
    supplied = [part.strip() for part in parts if part.strip()]
    if len(supplied) < 2:
        return "", "", ""
    return supplied[0], _name(*supplied[1:-1]), supplied[-1]


def _join(*parts: str, separator: str = "; ") -> str:
    return separator.join(part.strip() for part in parts if part.strip())


def _is_latin_name(value: str) -> bool:
    letters = [character for character in value if character.isalpha()]
    return bool(letters) and all(
        "LATIN" in unicodedata.name(character, "") for character in letters
    )


def _unique(values: Iterable[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        value = value.strip()
        if value and value not in seen:
            seen.add(value)
            result.append(value)
    return result


def _date_parts(element: Element) -> str:
    explicit = _text(element, "DATE", "DATE_OF_BIRTH")
    if explicit:
        return explicit
    year = _text(element, "YEAR")
    month = _text(element, "MONTH")
    day = _text(element, "DAY")
    if year:
        parts = [year]
        if month:
            parts.append(month.zfill(2))
        if day:
            parts.append(day.zfill(2))
        return "-".join(parts)
    start = _text(element, "FROM_YEAR")
    end = _text(element, "TO_YEAR")
    return _join(start, end, separator=" to ")


def _finish(element: Element) -> None:
    element.clear()
    parent = element.getparent()
    while parent is not None and element.getprevious() is not None:
        del parent[0]


def _mandatory_id(source: str, entity_id: str, seen: set[str]) -> bool:
    if not entity_id:
        LOGGER.warning("%s record without mandatory ID was skipped", source)
        return False
    if entity_id in seen:
        raise ValueError(f"Duplicate {source} ID: {entity_id}")
    seen.add(entity_id)
    return True


def parse_ofac(path: Path) -> NormalizedData:
    data = NormalizedData()
    seen: set[str] = set()
    context = etree.iterparse(str(path), events=("end",), recover=False, huge_tree=True)
    for _event, element in context:
        if _local(element).lower() != "sdnentry":
            continue
        entity_id = _text(element, "uid")
        if not _mandatory_id("ofac", entity_id, seen):
            _finish(element)
            continue
        first_name = _text(element, "firstName")
        last_name = _text(element, "lastName")
        primary = _name(first_name, last_name)
        primary_parts = (
            (first_name, "", last_name) if first_name and last_name else ("", "", "")
        )
        data.entities.append(
            Entity(
                entity_id=entity_id,
                source="ofac",
                record_type=_text(element, "sdnType") or "entity",
                primary_name=primary,
                comments=_text(element, "remarks"),
                primary_first_name=primary_parts[0],
                primary_middle_name=primary_parts[1],
                primary_last_name=primary_parts[2],
            )
        )
        for alias in _descendants(element, "aka"):
            alias_first = _text(alias, "firstName")
            alias_last = _text(alias, "lastName")
            value = _name(alias_first, alias_last)
            alias_parts = (
                (alias_first, "", alias_last)
                if alias_first and alias_last
                else ("", "", "")
            )
            if value and value != primary:
                data.aliases.append(
                    Alias(
                        entity_id,
                        value,
                        quality=_text(alias, "category") or _text(alias, "type"),
                        first_name=alias_parts[0],
                        middle_name=alias_parts[1],
                        last_name=alias_parts[2],
                    )
                )
        for address in _descendants(element, "address"):
            lines = [
                _text(address, "address1"),
                _text(address, "address2"),
                _text(address, "address3"),
                _text(address, "stateOrProvince"),
            ]
            data.addresses.append(
                Address(
                    entity_id,
                    address=_join(*lines),
                    country=_text(address, "country"),
                    city=_text(address, "city"),
                    postal_code=_text(address, "postalCode"),
                )
            )
        for program in _descendants(element, "program"):
            value = _node_text(program)
            if value:
                data.programs.append(Program(entity_id, value))
        for document in _descendants(element, "id"):
            number = _text(document, "idNumber")
            document_type = _text(document, "idType")
            country = _text(document, "idCountry")
            if number or document_type or country:
                data.documents.append(
                    Document(
                        entity_id,
                        number=number,
                        document_type=document_type,
                        issue_country=country,
                        issue_date=_text(document, "issueDate"),
                        expiration_date=_text(document, "expirationDate"),
                    )
                )
        for kind in ("nationality", "citizenship"):
            for item in _descendants(element, kind):
                value = _text(item, "country")
                if value:
                    data.nationalities.append(Nationality(entity_id, value, kind))
        for item in _descendants(element, "dateOfBirthItem"):
            value = _text(item, "dateOfBirth")
            if value:
                date_type = (
                    "primary" if _text(item, "mainEntry").lower() == "true" else ""
                )
                data.dates_of_birth.append(DateOfBirth(entity_id, value, date_type))
        for item in _descendants(element, "placeOfBirthItem"):
            value = _text(item, "placeOfBirth")
            if value:
                data.places_of_birth.append(PlaceOfBirth(entity_id, value))
        _finish(element)
    return data


def parse_un(path: Path) -> NormalizedData:
    data = NormalizedData()
    seen: set[str] = set()
    record_tags = {"individual", "entity"}
    context = etree.iterparse(str(path), events=("end",), recover=False, huge_tree=True)
    for _event, element in context:
        record_type = _local(element).lower()
        if record_type not in record_tags:
            continue
        entity_id = _text(element, "REFERENCE_NUMBER")
        if not _mandatory_id("un", entity_id, seen):
            _finish(element)
            continue
        primary_slots = (
            _text(element, "FIRST_NAME"),
            _text(element, "SECOND_NAME"),
            _text(element, "THIRD_NAME"),
            _text(element, "FOURTH_NAME"),
        )
        primary = _name(*primary_slots)
        primary_parts = _positional_name_parts(*primary_slots)
        original_name = _text(element, "NAME_ORIGINAL_SCRIPT")
        if not primary:
            primary = original_name
        updated = _join(
            *[_text(item, "VALUE") for item in _direct(element, "LAST_DAY_UPDATED")]
        )
        data.entities.append(
            Entity(
                entity_id=entity_id,
                source="un",
                record_type=record_type,
                primary_name=primary,
                comments=_text(element, "COMMENTS1"),
                date_listed=_text(element, "LISTED_ON"),
                date_updated=updated,
                primary_first_name=primary_parts[0],
                primary_middle_name=primary_parts[1],
                primary_last_name=primary_parts[2],
            )
        )
        if original_name and original_name != primary:
            data.aliases.append(
                Alias(entity_id, original_name, language="original script")
            )
        for alias in _direct(element, "INDIVIDUAL_ALIAS", "ENTITY_ALIAS"):
            value = _text(alias, "ALIAS_NAME")
            quality = _text(alias, "QUALITY")
            for alias_name in _unique(value.split("; ")):
                if alias_name != primary:
                    data.aliases.append(Alias(entity_id, alias_name, quality=quality))
        for address in _direct(element, "INDIVIDUAL_ADDRESS", "ENTITY_ADDRESS"):
            street = _join(
                _text(address, "STREET"),
                _text(address, "STATE_PROVINCE"),
                _text(address, "NOTE"),
            )
            data.addresses.append(
                Address(
                    entity_id,
                    address=street,
                    country=_text(address, "COUNTRY"),
                    city=_text(address, "CITY"),
                    postal_code=_text(address, "ZIP_CODE"),
                )
            )
        for document in _direct(element, "INDIVIDUAL_DOCUMENT"):
            number = _text(document, "NUMBER")
            document_type = _join(
                _text(document, "TYPE_OF_DOCUMENT"),
                _text(document, "TYPE_OF_DOCUMENT2"),
            )
            country = _text(document, "ISSUING_COUNTRY", "COUNTRY_OF_ISSUE")
            issue_date = _text(document, "DATE_OF_ISSUE")
            expiration = _text(document, "DATE_OF_EXPIRY")
            note = _text(document, "NOTE")
            if any((number, document_type, country, issue_date, expiration, note)):
                data.documents.append(
                    Document(
                        entity_id,
                        number=number,
                        document_type=document_type,
                        issue_country=country,
                        issue_date=issue_date,
                        expiration_date=expiration,
                        note=note,
                    )
                )
        for nationality in _direct(element, "NATIONALITY"):
            value = _text(nationality, "VALUE")
            if value:
                data.nationalities.append(Nationality(entity_id, value))
        for program in _unique([_text(element, "UN_LIST_TYPE")]):
            data.programs.append(Program(entity_id, program))
        for item in _direct(element, "INDIVIDUAL_DATE_OF_BIRTH"):
            value = _date_parts(item)
            if value:
                data.dates_of_birth.append(
                    DateOfBirth(
                        entity_id,
                        value,
                        date_type=_text(item, "TYPE_OF_DATE"),
                    )
                )
        for item in _direct(element, "INDIVIDUAL_PLACE_OF_BIRTH"):
            city = _text(item, "CITY", "CITY_OF_BIRTH")
            country = _text(item, "COUNTRY", "COUNTRY_OF_BIRTH")
            place = _text(item, "STATE_PROVINCE")
            if city or country or place:
                data.places_of_birth.append(
                    PlaceOfBirth(entity_id, place=place, country=country, city=city)
                )
        for item in _direct(element, "DESIGNATION"):
            value = _text(item, "VALUE") or _node_text(item)
            if value:
                data.designations.append(Designation(entity_id, value))
        _finish(element)
    return data


def _eu_name_remark(name_node: Element) -> str:
    return " ".join(_text(name_node, "remark").casefold().split())


def _eu_is_former_name(remark: str) -> bool:
    normalized = remark.lstrip("([ ")
    exact_labels = {
        "formerly known as",
        "former name",
        "maiden name",
        "formerly listed as",
        "previously listed as",
    }
    return (
        normalized in exact_labels
        or normalized.startswith(
            (
                "formerly known as ",
                "former name of ",
                "previous name of ",
                "maiden name:",
            )
        )
        or "; formerly listed" in normalized
        or "; previously listed" in normalized
        or "; as previously listed" in normalized
    )


def _eu_alias_quality(name_node: Element) -> str:
    remark = _eu_name_remark(name_node)
    if _eu_is_former_name(remark):
        return "former"
    if (
        _attr(name_node, "strong").lower() != "true"
        or "low quality alias" in remark
        or "lo quality alias" in remark
    ):
        return "weak"
    return "strong"


def _eu_is_explicit_alias(name_node: Element) -> bool:
    remark = _eu_name_remark(name_node)
    return (
        _eu_alias_quality(name_node) != "strong"
        or "alias" in remark
        or "a.k.a" in remark
        or "original script" in remark
    )


def _eu_primary_name_node(name_nodes: list[Element]) -> Element | None:
    named = [item for item in name_nodes if _attr(item, "wholeName")]
    preferred = [item for item in named if not _eu_is_explicit_alias(item)]
    strong = [item for item in named if _eu_alias_quality(item) == "strong"]

    for candidates in (preferred, strong, named):
        latin = [
            item for item in candidates if _is_latin_name(_attr(item, "wholeName"))
        ]
        english = [
            item for item in latin if _attr(item, "nameLanguage").casefold() == "en"
        ]
        if english:
            return english[0]
        without_language = [
            item for item in latin if not _attr(item, "nameLanguage").strip()
        ]
        if without_language:
            return without_language[0]
        if latin:
            return latin[0]
    return named[0] if named else None


def parse_eu(path: Path) -> NormalizedData:
    data = NormalizedData()
    seen: set[str] = set()
    context = etree.iterparse(str(path), events=("end",), recover=False, huge_tree=True)
    for _event, element in context:
        if _local(element).lower() != "sanctionentity":
            continue
        entity_id = _attr(element, "logicalId")
        if not _mandatory_id("eu", entity_id, seen):
            _finish(element)
            continue
        name_nodes = list(_direct(element, "nameAlias"))
        primary_node = _eu_primary_name_node(name_nodes)
        primary = _attr(primary_node, "wholeName") if primary_node is not None else ""
        if primary_node is not None:
            primary_parts = (
                _attr(primary_node, "firstName"),
                _attr(primary_node, "middleName"),
                _attr(primary_node, "lastName"),
            )
            if not primary:
                primary = _name(*primary_parts)
            if (
                not primary_parts[0]
                or not primary_parts[2]
                or _name(*primary_parts).casefold() != primary.casefold()
            ):
                primary_parts = ("", "", "")
        else:
            primary_parts = ("", "", "")
        subject = next(iter(_direct(element, "subjectType")), None)
        regulations = list(_direct(element, "regulation"))
        publication_dates = sorted(
            value
            for value in (_attr(item, "publicationDate") for item in regulations)
            if value
        )
        comments = _join(_attr(element, "designationDetails"), _text(element, "remark"))
        data.entities.append(
            Entity(
                entity_id=entity_id,
                source="eu",
                record_type=_attr(subject, "code") if subject is not None else "entity",
                primary_name=primary,
                comments=comments,
                date_listed=publication_dates[0] if publication_dates else "",
                date_updated=publication_dates[-1] if publication_dates else "",
                primary_first_name=primary_parts[0],
                primary_middle_name=primary_parts[1],
                primary_last_name=primary_parts[2],
            )
        )
        for alias in name_nodes:
            alias_parts = (
                _attr(alias, "firstName"),
                _attr(alias, "middleName"),
                _attr(alias, "lastName"),
            )
            value = _attr(alias, "wholeName") or _name(*alias_parts)
            if (
                not alias_parts[0]
                or not alias_parts[2]
                or _name(*alias_parts).casefold() != value.casefold()
            ):
                alias_parts = ("", "", "")
            if value and alias is not primary_node:
                data.aliases.append(
                    Alias(
                        entity_id,
                        value,
                        quality=_eu_alias_quality(alias),
                        language=_attr(alias, "nameLanguage")
                        or _attr(alias, "regulationLanguage"),
                        first_name=alias_parts[0],
                        middle_name=alias_parts[1],
                        last_name=alias_parts[2],
                    )
                )
        for address in _direct(element, "address"):
            value = _join(
                _attr(address, "street"),
                _attr(address, "poBox"),
                _attr(address, "place"),
                _attr(address, "region"),
            )
            data.addresses.append(
                Address(
                    entity_id,
                    address=value,
                    country=_attr(address, "countryDescription")
                    or _attr(address, "countryIso2Code"),
                    city=_attr(address, "city"),
                    postal_code=_attr(address, "zipCode"),
                )
            )
        for document in _direct(element, "identification"):
            number = _attr(document, "number") or _attr(document, "latinNumber")
            document_type = _attr(document, "identificationTypeDescription") or _attr(
                document, "identificationTypeCode"
            )
            country = _attr(document, "countryDescription") or _attr(
                document, "countryIso2Code"
            )
            note = _join(_attr(document, "issuedBy"), _text(document, "remark"))
            if number or document_type or country or note:
                data.documents.append(
                    Document(
                        entity_id,
                        number=number,
                        document_type=document_type,
                        issue_country=country,
                        note=note,
                    )
                )
        for citizenship in _direct(element, "citizenship"):
            value = _attr(citizenship, "countryDescription") or _attr(
                citizenship, "countryIso2Code"
            )
            if value:
                data.nationalities.append(
                    Nationality(entity_id, value, kind="citizenship")
                )
        for regulation in regulations:
            program = _attr(regulation, "programme")
            if program:
                data.programs.append(Program(entity_id, program))
            data.regulations.append(
                Regulation(
                    entity_id,
                    program=program,
                    number_title=_attr(regulation, "numberTitle"),
                    publication_date=_attr(regulation, "publicationDate"),
                    entry_into_force_date=_attr(regulation, "entryIntoForceDate"),
                    regulation_type=_attr(regulation, "regulationType"),
                    publication_url=_text(regulation, "publicationUrl"),
                )
            )
        for item in _direct(element, "birthdate"):
            value = _attr(item, "birthdate")
            if not value:
                year = _attr(item, "year")
                month = _attr(item, "monthOfYear")
                day = _attr(item, "dayOfMonth")
                value = "-".join(
                    [year, month.zfill(2), day.zfill(2)]
                    if year and month and day
                    else [part for part in (year, month, day) if part]
                )
            date_type = _attr(item, "calendarType")
            if _attr(item, "circa").lower() == "true":
                date_type = _join("circa", date_type)
            data.dates_of_birth.append(
                DateOfBirth(
                    entity_id,
                    value,
                    date_type=date_type,
                    country=_attr(item, "countryDescription")
                    or _attr(item, "countryIso2Code"),
                    city=_attr(item, "city"),
                    place=_join(_attr(item, "place"), _attr(item, "region")),
                )
            )
        for contact in _descendants(element, "contactInfo"):
            value = _attr(contact, "value")
            if value:
                data.contacts.append(
                    Contact(entity_id, _attr(contact, "key") or "contact", value)
                )
        designation = _attr(element, "designationDetails")
        if designation:
            data.designations.append(Designation(entity_id, designation))
        _finish(element)
    return data


def _uk_name(element: Element) -> str:
    components = [
        _text(element, "Name1"),
        _text(element, "Name2"),
        _text(element, "Name3"),
        _text(element, "Name4"),
        _text(element, "Name5"),
    ]
    last_or_full = _text(element, "Name6")
    if any(components):
        return _name(*components, last_or_full)
    return last_or_full


def _uk_name_parts(element: Element) -> tuple[str, str, str]:
    first = _text(element, "Name1")
    middle = _name(*[_text(element, f"Name{index}") for index in range(2, 6)])
    last = _text(element, "Name6")
    if first and last:
        return first, middle, last
    return "", "", ""


def _uk_alias_quality(element: Element) -> str:
    strength = " ".join(_text(element, "AliasStrength").casefold().split())
    if strength == "low quality a.k.a":
        return "weak"
    return "strong"


def parse_uk(path: Path) -> NormalizedData:
    data = NormalizedData()
    seen: set[str] = set()
    context = etree.iterparse(str(path), events=("end",), recover=False, huge_tree=True)
    for _event, element in context:
        if _local(element).lower() != "designation":
            continue
        entity_id = _text(element, "UniqueID", "Unique ID")
        if not _mandatory_id("uk", entity_id, seen):
            _finish(element)
            continue
        name_nodes = list(_descendants(element, "Name"))
        primary_node = next(
            (
                item
                for item in name_nodes
                if _text(item, "NameType").strip().lower() == "primary name"
                and _uk_name(item)
            ),
            next((item for item in name_nodes if _uk_name(item)), None),
        )
        primary = _uk_name(primary_node) if primary_node is not None else ""
        primary_parts = (
            _uk_name_parts(primary_node) if primary_node is not None else ("", "", "")
        )
        comments = _join(
            _text(element, "OtherInformation"),
            _text(element, "UKStatementofReasons"),
        )
        data.entities.append(
            Entity(
                entity_id=entity_id,
                source="uk",
                record_type=_text(element, "IndividualEntityShip") or "entity",
                primary_name=primary,
                comments=comments,
                date_listed=_text(element, "DateDesignated"),
                date_updated=_text(element, "LastUpdated"),
                primary_first_name=primary_parts[0],
                primary_middle_name=primary_parts[1],
                primary_last_name=primary_parts[2],
            )
        )
        for alias in name_nodes:
            value = _uk_name(alias)
            alias_parts = _uk_name_parts(alias)
            if value and alias is not primary_node:
                data.aliases.append(
                    Alias(
                        entity_id,
                        value,
                        quality=_uk_alias_quality(alias),
                        first_name=alias_parts[0],
                        middle_name=alias_parts[1],
                        last_name=alias_parts[2],
                    )
                )
        for alias in _descendants(element, "NonLatinName"):
            value = _text(alias, "NameNonLatinScript")
            if value and value != primary:
                data.aliases.append(
                    Alias(
                        entity_id,
                        value,
                        quality="strong",
                        language=_text(alias, "NonLatinScriptLanguage")
                        or "non-Latin script",
                    )
                )
        for address in _descendants(element, "Address"):
            lines = [_text(address, f"AddressLine{index}") for index in range(1, 7)]
            data.addresses.append(
                Address(
                    entity_id,
                    address=_join(*lines),
                    country=_text(address, "AddressCountry"),
                    city=_text(address, "AddressLine5"),
                    postal_code=_text(address, "AddressPostalCode"),
                )
            )
        for identifier in _descendants(element, "NationalIdentifier"):
            number = _text(identifier, "NationalIdentifierNumber")
            note = _text(identifier, "NationalIdentifierAdditionalInformation")
            if number or note:
                data.documents.append(
                    Document(
                        entity_id,
                        number=number,
                        document_type="National identifier",
                        note=note,
                    )
                )
        for passport in _descendants(element, "Passport"):
            number = _text(passport, "PassportNumber")
            note = _text(passport, "PassportAdditionalInformation")
            if number or note:
                data.documents.append(
                    Document(
                        entity_id,
                        number=number,
                        document_type="Passport",
                        note=note,
                    )
                )
        for tag, document_type in (
            ("BusinessRegistrationNumber", "Business registration number"),
            ("IMONumber", "IMO number"),
        ):
            for identifier in _descendants(element, tag):
                number = _node_text(identifier)
                if number:
                    data.documents.append(
                        Document(
                            entity_id,
                            number=number,
                            document_type=document_type,
                        )
                    )
        for nationality in _descendants(element, "Nationality"):
            value = _node_text(nationality)
            if value:
                data.nationalities.append(Nationality(entity_id, value))
        program = _text(element, "RegimeName")
        if program:
            data.programs.append(Program(entity_id, program))
        for location in _descendants(element, "Location"):
            city = _text(location, "TownOfBirth")
            country = _text(location, "CountryOfBirth")
            if city or country:
                data.places_of_birth.append(
                    PlaceOfBirth(entity_id, country=country, city=city)
                )
        designation = _text(element, "DesignationSource")
        if designation:
            data.designations.append(Designation(entity_id, designation))
        summary = _text(element, "SanctionsImposed")
        if summary:
            data.sanctions.append(Sanction(entity_id, summary, "summary"))
        indicators = next(iter(_direct(element, "SanctionsImposedIndicators")), None)
        if indicators is not None:
            for indicator in indicators:
                if _node_text(indicator).lower() == "true":
                    data.sanctions.append(
                        Sanction(entity_id, _local(indicator), "indicator")
                    )
        for tag, contact_type in (
            ("PhoneNumber", "phone"),
            ("EmailAddress", "email"),
            ("Website", "website"),
        ):
            for contact in _descendants(element, tag):
                value = _node_text(contact)
                if value:
                    data.contacts.append(Contact(entity_id, contact_type, value))
        _finish(element)
    return data


PARSERS: dict[str, Callable[[Path], NormalizedData]] = {
    "ofac": parse_ofac,
    "un": parse_un,
    "eu": parse_eu,
    "uk": parse_uk,
}


def parse(parser_name: str, path: Path) -> NormalizedData:
    try:
        parser = PARSERS[parser_name]
    except KeyError as exc:
        raise ValueError(
            f"No parser named {parser_name!r}. Available: {', '.join(PARSERS)}"
        ) from exc
    return parser(path)
