"""The closed lists of labels a contract and its parts can carry."""

from enum import StrEnum


class ClauseType(StrEnum):
    """What a part of a contract is about. `other` is the way out.

    These are best-effort labels. The line between two types is often a matter of
    judgement, and the model does not always draw it the same way twice.
    """

    PREAMBLE = "preamble"
    TABLE_OF_CONTENTS = "table_of_contents"
    DEFINITIONS = "definitions"
    SERVICES = "services"
    PAYMENT = "payment"
    TERM_AND_TERMINATION = "term_and_termination"
    CONFIDENTIALITY = "confidentiality"
    INTELLECTUAL_PROPERTY = "intellectual_property"
    WARRANTIES = "warranties"
    INDEMNIFICATION = "indemnification"
    LIMITATION_OF_LIABILITY = "limitation_of_liability"
    INSURANCE = "insurance"
    DATA_PROTECTION = "data_protection"
    COMPLIANCE = "compliance"
    FORCE_MAJEURE = "force_majeure"
    DISPUTE_RESOLUTION = "dispute_resolution"
    GOVERNING_LAW = "governing_law"
    NOTICES = "notices"
    ASSIGNMENT = "assignment"
    GENERAL_PROVISIONS = "general_provisions"
    SIGNATURE_BLOCK = "signature_block"
    EXHIBIT = "exhibit"
    OTHER = "other"


class ContractType(StrEnum):
    NDA = "nda"
    SERVICE_AGREEMENT = "service_agreement"
    MASTER_SERVICES_AGREEMENT = "master_services_agreement"
    LICENSE_AGREEMENT = "license_agreement"
    EMPLOYMENT_AGREEMENT = "employment_agreement"
    LEASE_AGREEMENT = "lease_agreement"
    SALES_AGREEMENT = "sales_agreement"
    OTHER = "other"
