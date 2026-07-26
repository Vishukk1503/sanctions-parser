from pathlib import Path

from sanctions_parser.parsers import parse_eu, parse_ofac, parse_uk, parse_un


def test_ofac_parser_normalizes_relations(tmp_path: Path) -> None:
    xml = tmp_path / "ofac.xml"
    xml.write_text(
        """<sdnList><sdnEntry><uid>42</uid><firstName>Ada</firstName>
        <lastName>Lovelace</lastName><programList><program>TEST</program></programList>
        <akaList><aka><firstName>A.</firstName><lastName>Lovelace</lastName></aka></akaList>
        <addressList><address><city>London</city><country>UK</country></address></addressList>
        <idList><id><idType>Passport</idType><idNumber>P42</idNumber>
        <idCountry>UK</idCountry></id></idList>
        <nationalityList><nationality><country>UK</country></nationality></nationalityList>
        <dateOfBirthList><dateOfBirthItem><dateOfBirth>10 Dec 1815</dateOfBirth>
        </dateOfBirthItem></dateOfBirthList>
        <placeOfBirthList><placeOfBirthItem><placeOfBirth>London, UK</placeOfBirth>
        </placeOfBirthItem></placeOfBirthList>
        </sdnEntry></sdnList>""",
        encoding="utf-8",
    )
    data = parse_ofac(xml)
    assert data.entities[0].entity_id == "42"
    assert data.entities[0].primary_name == "Ada Lovelace"
    assert data.programs[0].value == "TEST"
    assert data.addresses[0].city == "London"
    assert data.documents[0].number == "P42"
    assert data.nationalities[0].nationality == "UK"
    assert data.dates_of_birth[0].date_of_birth == "10 Dec 1815"
    assert data.places_of_birth[0].place == "London, UK"


def test_un_parser_maps_source_specific_relations(tmp_path: Path) -> None:
    xml = tmp_path / "un.xml"
    xml.write_text(
        """<CONSOLIDATED_LIST><INDIVIDUAL>
        <REFERENCE_NUMBER>UN.1</REFERENCE_NUMBER><FIRST_NAME>Ada</FIRST_NAME>
        <SECOND_NAME>Lovelace</SECOND_NAME><UN_LIST_TYPE>TEST</UN_LIST_TYPE>
        <LISTED_ON>2020-01-01</LISTED_ON>
        <LAST_DAY_UPDATED><VALUE>2024-02-03</VALUE></LAST_DAY_UPDATED>
        <INDIVIDUAL_ALIAS><QUALITY>Good</QUALITY>
        <ALIAS_NAME>A. Lovelace; Ada L.</ALIAS_NAME>
        </INDIVIDUAL_ALIAS>
        <INDIVIDUAL_ADDRESS><STREET>1 Main St</STREET><CITY>London</CITY>
        <COUNTRY>UK</COUNTRY></INDIVIDUAL_ADDRESS>
        <INDIVIDUAL_DOCUMENT><TYPE_OF_DOCUMENT>Passport</TYPE_OF_DOCUMENT>
        <NUMBER>P1</NUMBER><ISSUING_COUNTRY>UK</ISSUING_COUNTRY>
        </INDIVIDUAL_DOCUMENT>
        <NATIONALITY><VALUE>British</VALUE></NATIONALITY>
        <INDIVIDUAL_DATE_OF_BIRTH><TYPE_OF_DATE>EXACT</TYPE_OF_DATE>
        <YEAR>1815</YEAR></INDIVIDUAL_DATE_OF_BIRTH>
        <INDIVIDUAL_PLACE_OF_BIRTH><CITY>London</CITY><COUNTRY>UK</COUNTRY>
        </INDIVIDUAL_PLACE_OF_BIRTH>
        <DESIGNATION><VALUE>Mathematician</VALUE></DESIGNATION>
        </INDIVIDUAL></CONSOLIDATED_LIST>""",
        encoding="utf-8",
    )

    data = parse_un(xml)

    assert data.entities[0].primary_name == "Ada Lovelace"
    assert data.entities[0].date_updated == "2024-02-03"
    assert data.aliases[0].alias == "A. Lovelace"
    assert data.aliases[1].alias == "Ada L."
    assert data.aliases[1].quality == "Good"
    assert data.documents[0].document_type == "Passport"
    assert data.nationalities[0].nationality == "British"
    assert data.dates_of_birth[0].date_of_birth == "1815"
    assert data.places_of_birth[0].city == "London"
    assert data.designations[0].designation == "Mathematician"


def test_eu_parser_reads_attribute_based_fields(tmp_path: Path) -> None:
    xml = tmp_path / "eu.xml"
    xml.write_text(
        """<export xmlns="http://eu.example/export"><sanctionEntity logicalId="7"
        designationDetails="Listed person"><remark>Test remark</remark>
        <regulation programme="TEST" numberTitle="1/2024"
        publicationDate="2024-01-01" entryIntoForceDate="2024-01-02"
        regulationType="regulation"><publicationUrl>https://example.test/r</publicationUrl>
        </regulation><subjectType code="person"/>
        <nameAlias wholeName="Ada Lovelace" strong="true" nameLanguage="EN"/>
        <nameAlias wholeName="A. Lovelace" strong="false" nameLanguage="EN"/>
        <address street="1 Main St" city="London" zipCode="N1"
        countryDescription="UNITED KINGDOM"/>
        <citizenship countryDescription="UNITED KINGDOM"/>
        <identification number="P1" identificationTypeDescription="Passport"
        countryDescription="UNITED KINGDOM"/>
        <birthdate birthdate="1815-12-10" calendarType="GREGORIAN"
        city="London" countryDescription="UNITED KINGDOM"/>
        <contactInfo key="EMAIL" value="ada@example.test"/>
        </sanctionEntity></export>""",
        encoding="utf-8",
    )

    data = parse_eu(xml)

    assert data.entities[0].primary_name == "Ada Lovelace"
    assert data.entities[0].record_type == "person"
    assert data.aliases[0].alias == "A. Lovelace"
    assert data.addresses[0].address == "1 Main St"
    assert data.addresses[0].country == "UNITED KINGDOM"
    assert data.documents[0].number == "P1"
    assert data.nationalities[0].kind == "citizenship"
    assert data.regulations[0].number_title == "1/2024"
    assert data.dates_of_birth[0].date_of_birth == "1815-12-10"
    assert data.contacts[0].value == "ada@example.test"


def test_eu_parser_prioritizes_latin_primary_and_classifies_alias_remarks(
    tmp_path: Path,
) -> None:
    xml = tmp_path / "eu-names.xml"
    xml.write_text(
        """<export><sanctionEntity logicalId="8"><subjectType code="person"/>
        <nameAlias wholeName="English Alias" strong="true" nameLanguage="EN">
        <remark>Good quality alias</remark></nameAlias>
        <nameAlias wholeName="Иван Иванов" strong="true" nameLanguage="RU"/>
        <nameAlias wholeName="Ivan Ivanov" strong="true" nameLanguage="EN"/>
        <nameAlias wholeName="I. Ivanov" strong="true" nameLanguage="EN">
        <remark>low quality alias</remark></nameAlias>
        <nameAlias wholeName="Ivan Petrov" strong="true" nameLanguage="EN">
        <remark>formerly known as</remark></nameAlias>
        </sanctionEntity></export>""",
        encoding="utf-8",
    )

    data = parse_eu(xml)

    assert data.entities[0].primary_name == "Ivan Ivanov"
    assert [(item.alias, item.quality, item.language) for item in data.aliases] == [
        ("English Alias", "strong", "EN"),
        ("Иван Иванов", "strong", "RU"),
        ("I. Ivanov", "weak", "EN"),
        ("Ivan Petrov", "former", "EN"),
    ]


def test_eu_parser_does_not_treat_descriptive_former_text_as_a_former_name(
    tmp_path: Path,
) -> None:
    xml = tmp_path / "eu-former-description.xml"
    xml.write_text(
        """<export><sanctionEntity logicalId="9"><subjectType code="person"/>
        <nameAlias wholeName="Primary Person" strong="true" nameLanguage="EN"/>
        <nameAlias wholeName="Other Name" strong="true" nameLanguage="EN">
        <remark>Former Director of an organization</remark></nameAlias>
        </sanctionEntity></export>""",
        encoding="utf-8",
    )

    data = parse_eu(xml)

    assert data.entities[0].primary_name == "Primary Person"
    assert data.aliases[0].quality == "strong"


def test_uk_parser_maps_names_identifiers_and_sanctions(tmp_path: Path) -> None:
    xml = tmp_path / "uk.xml"
    xml.write_text(
        """<Designations><Designation><UniqueID>UK1</UniqueID>
        <LastUpdated>02/02/2024</LastUpdated><DateDesignated>01/01/2020</DateDesignated>
        <Names><Name><Name1>Ada</Name1><Name6>Lovelace</Name6>
        <NameType>Primary Name</NameType></Name>
        <Name><Name1>A.</Name1><Name6>Lovelace</Name6>
        <NameType>Alias</NameType></Name></Names>
        <NonLatinNames><NonLatinName><NameNonLatinScript>エイダ</NameNonLatinScript>
        </NonLatinName></NonLatinNames><RegimeName>Test regime</RegimeName>
        <IndividualEntityShip>Individual</IndividualEntityShip>
        <DesignationSource>UK</DesignationSource><SanctionsImposed>Asset freeze</SanctionsImposed>
        <SanctionsImposedIndicators><AssetFreeze>true</AssetFreeze>
        <TravelBan>false</TravelBan></SanctionsImposedIndicators>
        <Addresses><Address><AddressLine1>1 Main St</AddressLine1>
        <AddressLine5>London</AddressLine5><AddressPostalCode>N1</AddressPostalCode>
        <AddressCountry>United Kingdom</AddressCountry></Address></Addresses>
        <NationalIdentifierDetails><NationalIdentifier>
        <NationalIdentifierNumber>N1</NationalIdentifierNumber>
        </NationalIdentifier></NationalIdentifierDetails>
        <PassportDetails><Passport><PassportNumber>P1</PassportNumber></Passport>
        </PassportDetails><Nationalities><Nationality>British</Nationality></Nationalities>
        <BirthDetails><Location><TownOfBirth>London</TownOfBirth>
        <CountryOfBirth>United Kingdom</CountryOfBirth></Location></BirthDetails>
        <PhoneNumbers><PhoneNumber>123</PhoneNumber></PhoneNumbers>
        </Designation></Designations>""",
        encoding="utf-8",
    )

    data = parse_uk(xml)

    assert data.entities[0].primary_name == "Ada Lovelace"
    assert len(data.aliases) == 2
    assert data.addresses[0].country == "United Kingdom"
    assert {item.number for item in data.documents} == {"N1", "P1"}
    assert data.nationalities[0].nationality == "British"
    assert data.places_of_birth[0].city == "London"
    assert data.programs[0].program_name == "Test regime"
    assert len(data.sanctions) == 2
    assert data.contacts[0].contact_type == "phone"


def test_uk_parser_uses_alias_strength_and_preserves_non_latin_language(
    tmp_path: Path,
) -> None:
    xml = tmp_path / "uk-alias-strength.xml"
    xml.write_text(
        """<Designations><Designation><UniqueID>UK2</UniqueID>
        <Names>
        <Name><Name1>Primary</Name1><Name6>Person</Name6>
        <NameType>Primary Name</NameType></Name>
        <Name><Name6>Strong Alias</Name6><NameType>Alias</NameType>
        <AliasStrength>Good quality a.k.a</AliasStrength></Name>
        <Name><Name6>Weak Alias</Name6><NameType>Alias</NameType>
        <AliasStrength>Low quality a.k.a</AliasStrength></Name>
        <Name><Name6>Name Variation</Name6>
        <NameType>Primary Name Variation</NameType></Name>
        </Names>
        <NonLatinNames><NonLatinName>
        <NameNonLatinScript>Иван Иванов</NameNonLatinScript>
        <NonLatinScriptLanguage>Russian</NonLatinScriptLanguage>
        </NonLatinName></NonLatinNames>
        <IndividualEntityShip>Individual</IndividualEntityShip>
        </Designation></Designations>""",
        encoding="utf-8",
    )

    data = parse_uk(xml)

    assert data.entities[0].primary_name == "Primary Person"
    assert [(item.alias, item.quality, item.language) for item in data.aliases] == [
        ("Strong Alias", "strong", ""),
        ("Weak Alias", "weak", ""),
        ("Name Variation", "strong", ""),
        ("Иван Иванов", "strong", "Russian"),
    ]
