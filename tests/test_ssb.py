from pathlib import Path

from sanctions_parser.models import Alias, Entity, NormalizedData
from sanctions_parser.ssb import SSB_COLUMNS, SSB_HEADER, export_ssb


def _fields(path: Path) -> list[list[str]]:
    return [line.split("|") for line in path.read_text(encoding="utf-8").splitlines()]


def test_ssb_exports_strict_individual_and_organization_files(tmp_path: Path) -> None:
    data = NormalizedData(
        entities=[
            Entity(
                "001",
                "uk",
                "Individual",
                "Ada Middle Lovelace",
                primary_first_name="Ada",
                primary_middle_name="Middle",
                primary_last_name="Lovelace",
            ),
            Entity("002", "uk", "Individual", "Complete Individual Name"),
            Entity("003", "uk", "Entity", "Example | Company\nLimited"),
            Entity("004", "uk", "Ship", "Excluded Ship"),
        ],
        aliases=[
            Alias(
                "001",
                "Ada M. Lovelace",
                "Good",
                first_name="Ada",
                middle_name="M.",
                last_name="Lovelace",
            ),
            Alias("001", "Complete Alias", "weak"),
            Alias("001", "Former Alias", "f.k.a."),
            Alias("001", "Fourth Alias", "weak"),
            Alias("002", "Only Full Alias", "strong"),
            Alias("003", "Example | Co.\tLtd", "strong"),
        ],
    )

    result = export_ssb(data, tmp_path)

    assert result.individuals == 2
    assert result.organizations == 1
    assert result.excluded == 1
    assert result.aliases_omitted == 1
    assert result.individual_path.name == "uk-individuals.csv"
    assert result.organization_path.name == "uk-organizations.csv"

    individuals = _fields(result.individual_path)
    organizations = _fields(result.organization_path)
    assert individuals[0] == SSB_COLUMNS
    assert organizations[0] == SSB_COLUMNS
    assert SSB_HEADER.count("|") == 82
    assert all(len(row) == 83 for row in individuals + organizations)
    assert all(line.count("|") == 82 for line in result.individual_path.read_text(
        encoding="utf-8"
    ).splitlines())

    first = dict(zip(SSB_COLUMNS, individuals[1], strict=True))
    assert first["PartyKey"] == "UK_001"
    assert first["PartyType"] == "Individual"
    assert first["PartyId1Type"] == "UK"
    assert first["PartyId1Value"] == "001"
    assert first["PrimaryFirstName"] == "Ada"
    assert first["PrimaryMiddleName"] == "Middle"
    assert first["PrimaryLastName"] == "Lovelace"
    assert first["PrimaryFullName"] == ""
    assert first["PrimaryIsBrokenName"] == "true"
    assert first["Alias1FirstName"] == "Ada"
    assert first["Alias1MiddleName"] == "M."
    assert first["Alias1LastName"] == "Lovelace"
    assert first["Alias1IsBrokenName"] == "true"
    assert first["Alias2LastName"] == "Complete Alias"
    assert first["Alias3LastName"] == "Fourth Alias"

    complete = dict(zip(SSB_COLUMNS, individuals[2], strict=True))
    assert complete["PrimaryLastName"] == "Complete Individual Name"
    assert complete["PrimaryIsBrokenName"] == ""
    assert complete["Alias1LastName"] == "Only Full Alias"

    organization = dict(zip(SSB_COLUMNS, organizations[1], strict=True))
    assert organization["PartyKey"] == "UK_003"
    assert organization["PrimaryFullName"] == "Example   Company Limited"
    assert organization["Alias1FullName"] == "Example   Co. Ltd"
    assert organization["PrimaryLastName"] == ""
    assert organization["CustomField16- SourceSystemCode"] == "UK"


def test_ssb_prefers_strong_then_weak_then_former_aliases(tmp_path: Path) -> None:
    data = NormalizedData(
        entities=[Entity("A1", "eu", "enterprise", "Example")],
        aliases=[
            Alias("A1", "Former", "former"),
            Alias("A1", "Weak", "weak"),
            Alias("A1", "Strong", "strong"),
            Alias("A1", "Another strong", "strong"),
        ],
    )

    result = export_ssb(data, tmp_path)
    row = dict(zip(SSB_COLUMNS, _fields(result.organization_path)[1], strict=True))

    assert row["Alias1FullName"] == "Strong"
    assert row["Alias2FullName"] == "Another strong"
    assert row["Alias3FullName"] == "Weak"
    assert result.aliases_omitted == 1
