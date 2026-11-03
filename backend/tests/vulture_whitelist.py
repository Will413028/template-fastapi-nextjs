"""Attributes vulture cannot see being read: openapi-core reads them through its Request and Response protocols.

Passed to vulture by `make deadcode` (backend/Makefile); nothing imports or runs this module.
"""

from apitest import ContractResponse
from app.web.contract import ContractRequest

_ = (ContractRequest.host_url, ContractRequest.parameters, ContractResponse.data)
