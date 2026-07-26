from __future__ import annotations

from dataclasses import asdict, dataclass, field, fields
from typing import Any


@dataclass
class Entity:
    entity_id: str
    source: str
    record_type: str
    primary_name: str = ""
    status: str = ""
    comments: str = ""
    date_listed: str = ""
    date_updated: str = ""


@dataclass
class Child:
    """Backward-compatible generic child row.

    New parser code uses the typed relation classes below. This class remains
    available for callers that imported it from earlier framework versions.
    """

    entity_id: str
    value: str
    kind: str = ""
    country: str = ""
    city: str = ""
    postal_code: str = ""
    quality: str = ""
    language: str = ""


@dataclass
class Alias:
    entity_id: str
    alias: str
    quality: str = ""
    language: str = ""

    @property
    def value(self) -> str:
        return self.alias


@dataclass
class Address:
    entity_id: str
    address: str = ""
    country: str = ""
    city: str = ""
    postal_code: str = ""

    @property
    def value(self) -> str:
        return self.address


@dataclass
class Document:
    entity_id: str
    number: str = ""
    document_type: str = ""
    issue_country: str = ""
    issue_date: str = ""
    expiration_date: str = ""
    note: str = ""

    @property
    def value(self) -> str:
        return self.number

    @property
    def kind(self) -> str:
        return self.document_type

    @property
    def country(self) -> str:
        return self.issue_country


@dataclass
class Nationality:
    entity_id: str
    nationality: str
    kind: str = "nationality"

    @property
    def value(self) -> str:
        return self.nationality


@dataclass
class Program:
    entity_id: str
    program_name: str

    @property
    def value(self) -> str:
        return self.program_name


@dataclass
class Relationship:
    entity_id: str
    related_entity: str
    relationship_type: str = ""

    @property
    def value(self) -> str:
        return self.related_entity


@dataclass
class DateOfBirth:
    entity_id: str
    date_of_birth: str = ""
    date_type: str = ""
    country: str = ""
    city: str = ""
    place: str = ""

    @property
    def value(self) -> str:
        return self.date_of_birth


@dataclass
class PlaceOfBirth:
    entity_id: str
    place: str = ""
    country: str = ""
    city: str = ""

    @property
    def value(self) -> str:
        return self.place


@dataclass
class Designation:
    entity_id: str
    designation: str

    @property
    def value(self) -> str:
        return self.designation


@dataclass
class Regulation:
    entity_id: str
    program: str = ""
    number_title: str = ""
    publication_date: str = ""
    entry_into_force_date: str = ""
    regulation_type: str = ""
    publication_url: str = ""

    @property
    def value(self) -> str:
        return self.number_title or self.program


@dataclass
class Contact:
    entity_id: str
    contact_type: str
    value: str


@dataclass
class Sanction:
    entity_id: str
    sanction: str
    kind: str = ""

    @property
    def value(self) -> str:
        return self.sanction


@dataclass
class NormalizedData:
    entities: list[Entity] = field(default_factory=list)
    aliases: list[Alias] = field(default_factory=list)
    addresses: list[Address] = field(default_factory=list)
    documents: list[Document] = field(default_factory=list)
    nationalities: list[Nationality] = field(default_factory=list)
    programs: list[Program] = field(default_factory=list)
    relationships: list[Relationship] = field(default_factory=list)
    dates_of_birth: list[DateOfBirth] = field(default_factory=list)
    places_of_birth: list[PlaceOfBirth] = field(default_factory=list)
    designations: list[Designation] = field(default_factory=list)
    regulations: list[Regulation] = field(default_factory=list)
    contacts: list[Contact] = field(default_factory=list)
    sanctions: list[Sanction] = field(default_factory=list)

    def _relations(self) -> dict[str, list[Any]]:
        return {
            "entity": self.entities,
            "alias": self.aliases,
            "address": self.addresses,
            "document": self.documents,
            "nationality": self.nationalities,
            "program": self.programs,
            "relationship": self.relationships,
            "date_of_birth": self.dates_of_birth,
            "place_of_birth": self.places_of_birth,
            "designation": self.designations,
            "regulation": self.regulations,
            "contact": self.contacts,
            "sanction": self.sanctions,
        }

    def tables(self) -> dict[str, list[dict[str, str]]]:
        return {
            name: [asdict(row) for row in rows]
            for name, rows in self._relations().items()
        }

    def schemas(self) -> dict[str, list[str]]:
        """Return stable columns even when a relation has no rows."""
        relation_types = {
            "entity": Entity,
            "alias": Alias,
            "address": Address,
            "document": Document,
            "nationality": Nationality,
            "program": Program,
            "relationship": Relationship,
            "date_of_birth": DateOfBirth,
            "place_of_birth": PlaceOfBirth,
            "designation": Designation,
            "regulation": Regulation,
            "contact": Contact,
            "sanction": Sanction,
        }
        return {
            name: [item.name for item in fields(row_type)]
            for name, row_type in relation_types.items()
        }
