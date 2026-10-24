"""Finto modulo objc per il banco di prova su Linux."""
import builtins

super = builtins.super


def python_method(f):
    f.__python_method__ = True
    return f


def protocolNamed(nome):
    return nome


class ProtocolError(Exception):
    pass
