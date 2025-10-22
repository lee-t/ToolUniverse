"""OLS API tool for ToolUniverse.

This module exposes the Ontology Lookup Service (OLS) endpoints that were
previously available through the dedicated MCP server. The MCP tooling has been
adapted into a synchronous local tool that fits the ToolUniverse runtime.
"""

from __future__ import annotations

import urllib.parse
from typing import Any, Dict, List, Optional

import requests
from pydantic import BaseModel, Field, HttpUrl, ValidationError

from .base_tool import BaseTool
from .tool_registry import register_tool

OLS_BASE_URL = "https://www.ebi.ac.uk/ols4"
REQUEST_TIMEOUT = 30.0


def url_encode_iri(iri: str) -> str:
    """Double URL encode an IRI as required by the OLS API."""

    return urllib.parse.quote(urllib.parse.quote(iri, safe=""), safe="")


class OntologyInfo(BaseModel):
    """Description of a single ontology entry in OLS."""

    id: str = Field(
        ..., description="Unique identifier for the ontology", alias="ontologyId"
    )
    title: str = Field(..., description="Name of the ontology")
    version: Optional[str] = Field(None, description="Version of the ontology")
    description: Optional[str] = Field(None, description="Description of the ontology")
    domain: Optional[str] = Field(None, description="Domain of the ontology")
    homepage: Optional[HttpUrl] = Field(None, description="URL for the ontology")
    preferred_prefix: Optional[str] = Field(
        None, description="Preferred prefix for the ontology", alias="preferredPrefix"
    )
    number_of_terms: Optional[int] = Field(
        None, description="Number of terms in the ontology"
    )
    number_of_classes: Optional[int] = Field(
        None, description="Number of classes in the ontology", alias="numberOfClasses"
    )
    repository: Optional[HttpUrl] = Field(
        None, description="Repository URL for the ontology"
    )


class PagedResponse(BaseModel):
    """Base structure for paginated responses returned by OLS."""

    total_elements: int = Field(
        0, description="Total number of items", alias="totalElements"
    )
    page: int = Field(0, description="Current page number")
    size: int = Field(
        20, description="Number of items in current page", alias="numElements"
    )
    total_pages: int = Field(0, description="Total number of pages", alias="totalPages")


class OntologySearchResponse(PagedResponse):
    """Paginated collection of ontologies returned by the search endpoint."""

    ontologies: List[OntologyInfo] = Field(
        ..., description="List of ontologies matching the search criteria"
    )


class TermInfo(BaseModel):
    """Basic term representation returned by OLS."""

    model_config = {"populate_by_name": True}

    iri: HttpUrl = Field(..., description="IRI of the term")
    ontology_name: str = Field(
        ...,
        description="Name of the ontology containing the term",
        alias="ontologyName",
    )
    short_form: str = Field(
        ..., description="Short form identifier for the term", alias="shortForm"
    )
    label: str = Field(..., description="Human-readable label for the term")
    obo_id: Optional[str] = Field(
        None, description="OBOLibrary ID for the term", alias="oboId"
    )
    is_obsolete: Optional[bool] = Field(
        False, description="Indicates if the term is obsolete", alias="isObsolete"
    )


class TermSearchResponse(PagedResponse):
    """Paginated set of OLS terms."""

    num_found: int = Field(
        0, description="Total number of terms found", alias="numFound"
    )
    terms: List[TermInfo] = Field(
        ..., description="List of terms matching the search criteria"
    )


class DetailedTermInfo(TermInfo):
    """Extended term details in OLS."""

    description: Optional[List[str]] = Field(None, description="Definition of the term")
    synonyms: Optional[List[str]] = Field(
        None, description="List of synonyms for the term"
    )


@register_tool("OLSTool")
class OLSTool(BaseTool):
    """Interact with the EMBL-EBI Ontology Lookup Service (OLS) REST API."""

    _OPERATIONS = {
        "search_terms": "_handle_search_terms",
        "get_ontology_info": "_handle_get_ontology_info",
        "search_ontologies": "_handle_search_ontologies",
        "get_term_info": "_handle_get_term_info",
        "get_term_children": "_handle_get_term_children",
        "get_term_ancestors": "_handle_get_term_ancestors",
        "find_similar_terms": "_handle_find_similar_terms",
    }

    def __init__(self, tool_config):
        super().__init__(tool_config)
        self.base_url = tool_config.get("base_url", OLS_BASE_URL).rstrip("/")
        self.timeout = tool_config.get("timeout", REQUEST_TIMEOUT)
        self.session = requests.Session()

    def __del__(self):
        try:
            self.session.close()
        except Exception:
            pass

    def run(self, arguments=None, **_: Any):
        """Dispatch the requested OLS operation."""

        arguments = arguments or {}
        operation = arguments.get("operation")
        if not operation:
            return {
                "error": "`operation` argument is required.",
                "available_operations": sorted(self._OPERATIONS.keys()),
            }

        handler_name = self._OPERATIONS.get(operation)
        if not handler_name:
            return {
                "error": f"Unsupported operation '{operation}'.",
                "available_operations": sorted(self._OPERATIONS.keys()),
            }

        handler = getattr(self, handler_name)
        try:
            return handler(arguments)
        except requests.RequestException as exc:
            return {"error": "OLS API request failed.", "details": str(exc)}
        except ValidationError as exc:
            return {
                "error": "Failed to validate OLS response.",
                "details": exc.errors(),
            }

    def _handle_search_terms(self, arguments: Dict[str, Any]) -> Dict[str, Any]:
        query = arguments.get("query")
        if not query:
            return {"error": "`query` parameter is required for `search_terms`."}

        rows = int(arguments.get("rows", 10))
        ontology = arguments.get("ontology")
        exact_match = bool(arguments.get("exact_match", False))
        include_obsolete = bool(arguments.get("include_obsolete", False))

        params = {
            "q": query,
            "rows": rows,
            "start": 0,
            "exact": exact_match,
            "obsoletes": include_obsolete,
        }
        if ontology:
            params["ontology"] = ontology

        data = self._get_json("/api/search", params=params)
        formatted = self._format_term_collection(data, rows)
        formatted["query"] = query
        formatted["filters"] = {
            "ontology": ontology,
            "exact_match": exact_match,
            "include_obsolete": include_obsolete,
        }
        return formatted

    def _handle_get_ontology_info(self, arguments: Dict[str, Any]) -> Dict[str, Any]:
        ontology_id = arguments.get("ontology_id")
        if not ontology_id:
            return {
                "error": "`ontology_id` parameter is required for `get_ontology_info`."
            }

        data = self._get_json(f"/api/v2/ontologies/{ontology_id}")
        ontology = OntologyInfo.model_validate(data)
        return ontology.model_dump(by_alias=True)

    def _handle_search_ontologies(self, arguments: Dict[str, Any]) -> Dict[str, Any]:
        search = arguments.get("search")
        page = int(arguments.get("page", 0))
        size = int(arguments.get("size", 20))

        params: Dict[str, Any] = {"page": page, "size": size}
        if search:
            params["search"] = search

        data = self._get_json("/api/v2/ontologies", params=params)
        embedded = data.get("_embedded", {})
        ontologies = embedded.get("ontologies", [])

        validated: List[Dict[str, Any]] = []
        for item in ontologies:
            try:
                validated.append(
                    OntologyInfo.model_validate(item).model_dump(by_alias=True)
                )
            except ValidationError:
                continue

        page_info = data.get("page", {})
        return {
            "results": validated or ontologies,
            "pagination": {
                "page": page_info.get("number", page),
                "size": page_info.get("size", size),
                "total_pages": page_info.get("totalPages", 0),
                "total_items": page_info.get("totalElements", len(ontologies)),
            },
            "search": search,
        }

    def _handle_get_term_info(self, arguments: Dict[str, Any]) -> Dict[str, Any]:
        identifier = arguments.get("id")
        if not identifier:
            return {"error": "`id` parameter is required for `get_term_info`."}

        data = self._get_json("/api/terms", params={"id": identifier})
        embedded = data.get("_embedded", {})
        terms = embedded.get("terms") if isinstance(embedded, dict) else None
        if not terms:
            return {"error": f"Term with ID '{identifier}' was not found in OLS."}

        # Normalize the term data before validation
        term_data = terms[0]
        if "ontologyId" in term_data and "ontologyName" not in term_data:
            term_data["ontologyName"] = term_data["ontologyId"]

        term = DetailedTermInfo.model_validate(term_data)
        return term.model_dump(by_alias=True)

    def _handle_get_term_children(self, arguments: Dict[str, Any]) -> Dict[str, Any]:
        term_iri = arguments.get("term_iri")
        ontology = arguments.get("ontology")
        if not term_iri or not ontology:
            return {
                "error": "`term_iri` and `ontology` parameters are required for `get_term_children`."
            }

        include_obsolete = bool(arguments.get("include_obsolete", False))
        size = int(arguments.get("size", 20))
        encoded = url_encode_iri(term_iri)

        params = {
            "page": 0,
            "size": size,
            "includeObsoleteEntities": include_obsolete,
        }

        data = self._get_json(
            f"/api/v2/ontologies/{ontology}/classes/{encoded}/children", params=params
        )
        formatted = self._format_term_collection(data, size)
        formatted["term_iri"] = term_iri
        formatted["ontology"] = ontology
        formatted["filters"] = {"include_obsolete": include_obsolete}
        return formatted

    def _handle_get_term_ancestors(self, arguments: Dict[str, Any]) -> Dict[str, Any]:
        term_iri = arguments.get("term_iri")
        ontology = arguments.get("ontology")
        if not term_iri or not ontology:
            return {
                "error": "`term_iri` and `ontology` parameters are required for `get_term_ancestors`."
            }

        include_obsolete = bool(arguments.get("include_obsolete", False))
        size = int(arguments.get("size", 20))
        encoded = url_encode_iri(term_iri)

        params = {
            "page": 0,
            "size": size,
            "includeObsoleteEntities": include_obsolete,
        }

        data = self._get_json(
            f"/api/v2/ontologies/{ontology}/classes/{encoded}/ancestors", params=params
        )
        formatted = self._format_term_collection(data, size)
        formatted["term_iri"] = term_iri
        formatted["ontology"] = ontology
        formatted["filters"] = {"include_obsolete": include_obsolete}
        return formatted

    def _handle_find_similar_terms(self, arguments: Dict[str, Any]) -> Dict[str, Any]:
        term_iri = arguments.get("term_iri")
        ontology = arguments.get("ontology")
        if not term_iri or not ontology:
            return {
                "error": "`term_iri` and `ontology` parameters are required for `find_similar_terms`."
            }

        size = int(arguments.get("size", 10))
        encoded = url_encode_iri(term_iri)

        params = {"page": 0, "size": size}
        data = self._get_json(
            f"/api/v2/ontologies/{ontology}/classes/{encoded}/llm_similar",
            params=params,
        )
        formatted = self._format_term_collection(data, size)
        formatted["term_iri"] = term_iri
        formatted["ontology"] = ontology
        return formatted

    def _get_json(
        self, path: str, params: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        url = f"{self.base_url}{path}"
        response = self.session.get(url, params=params, timeout=self.timeout)
        response.raise_for_status()
        return response.json()

    def _format_term_collection(
        self, data: Dict[str, Any], size: int
    ) -> Dict[str, Any]:
        elements: Optional[List[Dict[str, Any]]] = None

        if isinstance(data, dict):
            if "elements" in data and isinstance(data["elements"], list):
                elements = data["elements"]
            else:
                embedded = data.get("_embedded")
                if isinstance(embedded, dict):
                    for key in ("terms", "children", "ancestors"):
                        if key in embedded and isinstance(embedded[key], list):
                            elements = embedded[key]
                            break
                    if elements is None:
                        candidates = [
                            value
                            for value in embedded.values()
                            if isinstance(value, list)
                        ]
                        if candidates:
                            elements = candidates[0]

        if not elements:
            return data if isinstance(data, dict) else {"items": data}

        limited = elements[:size]
        term_models = [self._build_term_model(item) for item in limited]
        term_models = [model for model in term_models if model is not None]

        total = (
            data.get("totalElements")
            or data.get("page", {}).get("totalElements")
            or len(elements)
        )

        result: Dict[str, Any] = {
            "terms": [model.model_dump(by_alias=True) for model in term_models],
            "total_items": total,
            "showing": len(term_models),
        }

        page_info = data.get("page") if isinstance(data, dict) else None
        if isinstance(page_info, dict):
            result["pagination"] = {
                "page": page_info.get("number", 0),
                "size": page_info.get("size", len(limited)),
                "total_pages": page_info.get("totalPages", 0),
                "total_items": page_info.get("totalElements", total),
            }

        return result

    @staticmethod
    def _build_term_model(item: Dict[str, Any]) -> Optional[TermInfo]:
        payload = {
            "iri": item.get("iri"),
            "ontology_name": item.get("ontologyName")
            or item.get("ontology_name")
            or item.get("ontologyId")
            or "",
            "short_form": item.get("shortForm") or item.get("short_form") or "",
            "label": item.get("label") or "",
            "oboId": item.get("oboId") or item.get("obo_id"),
            "isObsolete": item.get("isObsolete") or item.get("is_obsolete", False),
        }

        if not payload["iri"]:
            return None

        try:
            return TermInfo.model_validate(payload)
        except ValidationError:
            return None


__all__ = [
    "OLSTool",
    "OntologyInfo",
    "OntologySearchResponse",
    "TermInfo",
    "TermSearchResponse",
    "DetailedTermInfo",
    "url_encode_iri",
]
