"""
Type definitions for Omnistrate Fleet API.
"""


class Service:
    """Represents an Omnistrate service."""

    def __init__(
        self,
        id: str,
        name: str,
        key: str,
        service_provider_id: str,
        environments: list["Environment"],
    ):
        self.id = id
        self.name = name
        self.key = key
        self.service_provider_id = service_provider_id
        self.environments = environments

    def get_environment(self, environment_id: str):
        """Get environment by ID."""
        return next(env for env in self.environments if env.id == environment_id)

    @staticmethod
    def from_json(json: dict):
        """Create Service from JSON response."""
        return Service(
            json["id"],
            json["name"],
            json["key"],
            json["serviceProviderID"],
            [Environment.from_json(env) for env in json["serviceEnvironments"]],
        )


class ServiceModel:
    """Represents an Omnistrate service model (topology)."""

    def __init__(self, id: str, name: str, key: str):
        self.id = id
        self.name = name
        self.key = key

    @staticmethod
    def from_json(json: dict):
        """Create ServiceModel from JSON response."""
        return ServiceModel(json["id"], json["name"], json["key"])


class Environment:
    """Represents an Omnistrate environment."""

    def __init__(self, id: str, name: str, key: str):
        self.id = id
        self.name = name
        self.key = key.lower()

    @staticmethod
    def from_json(json: dict):
        """Create Environment from JSON response."""
        return Environment(json["id"], json["name"], json["name"])


class ProductTier:
    """Represents an Omnistrate product tier."""

    def __init__(
        self,
        product_tier_id: str,
        product_tier_name: str,
        product_tier_key: str,
        latest_major_version: str,
        service_model_id: str,
        service_model_name: str,
        service_environment_id: str,
        service_api_id: str,
    ):
        self.product_tier_id = product_tier_id
        self.product_tier_name = product_tier_name
        self.product_tier_key = product_tier_key
        self.latest_major_version = latest_major_version
        self.service_model_id = service_model_id
        self.service_model_name = service_model_name
        self.service_environment_id = service_environment_id
        self.service_api_id = service_api_id

    @staticmethod
    def from_json(json: dict):
        """Create ProductTier from JSON response."""
        return ProductTier(
            json["productTierId"],
            json["productTierName"],
            json["productTierKey"],
            json["latestMajorVersion"],
            json["serviceModelId"],
            json["serviceModelName"],
            json["serviceEnvironmentId"],
            json["serviceApiId"],
        )


class TierVersionStatus:
    """Tier version status constants."""

    PREFERRED = "Preferred"
    ACTIVE = "Active"
    DEPRECATED = "Deprecated"

    @staticmethod
    def from_string(status: str):
        """Parse status from string."""
        if status == "Preferred":
            return TierVersionStatus.PREFERRED
        if status == "Active":
            return TierVersionStatus.ACTIVE
        if status == "Deprecated":
            return TierVersionStatus.DEPRECATED
        raise ValueError(f"Invalid status: {status}")


class OmnistrateTierVersion:
    """Represents a tier version."""

    def __init__(
        self,
        version: str,
        service_id: str,
        product_tier_id: str,
        status: TierVersionStatus,
        description: str,
    ):
        self.version = version
        self.service_id = service_id
        self.product_tier_id = product_tier_id
        self.status = status
        self.description = description

    @staticmethod
    def from_json(json: dict):
        """Create OmnistrateTierVersion from JSON response."""
        return OmnistrateTierVersion(
            json["version"],
            json["serviceId"],
            json["productTierId"],
            TierVersionStatus.from_string(json["status"]),
            json["description"],
        )
