"""Pluggable CRM adapters. GoHighLevel is the first implementation."""

from app.crm.base import CRMAdapter, CrmContact, CrmResult, NullCRMAdapter
from app.crm.registry import build_crm

__all__ = ["CRMAdapter", "CrmContact", "CrmResult", "NullCRMAdapter", "build_crm"]
