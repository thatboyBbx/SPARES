"""Centralized role groups.

A single named source of truth for "who can do X" instead of inlining ad
hoc role tuples at every route in main.py.
"""
from app.models import UserRole

CAN_MANAGE_STOCK_RECEIPTS = (UserRole.OWNER, UserRole.STORE_KEEPER, UserRole.SUPER_ADMIN)
CAN_REQUEST_TRANSFER = (UserRole.OWNER, UserRole.STORE_KEEPER, UserRole.SHOP_MANAGER, UserRole.SUPER_ADMIN)
CAN_FULFIL_TRANSFER = (UserRole.OWNER, UserRole.STORE_KEEPER, UserRole.SUPER_ADMIN)
CAN_RECEIVE_TRANSFER = (UserRole.OWNER, UserRole.STORE_KEEPER, UserRole.SHOP_MANAGER, UserRole.SUPER_ADMIN)
CAN_RECORD_SALE = (UserRole.OWNER, UserRole.CASHIER, UserRole.SHOP_MANAGER, UserRole.SUPER_ADMIN)
CAN_APPROVE = (UserRole.OWNER, UserRole.ACCOUNTANT, UserRole.SUPER_ADMIN)
CAN_MANAGE_USERS = (UserRole.OWNER, UserRole.SUPER_ADMIN)
CAN_REQUEST_PURCHASE = (UserRole.OWNER, UserRole.STORE_KEEPER, UserRole.SHOP_MANAGER, UserRole.SUPER_ADMIN)
