"""Network code of HamQ: cty.dat download, WSJT-X UDP listener, Hamlib clients.

Everything here is asynchronous (Qt sockets and ``QgsNetworkAccessManager``)
so the QGIS user interface never blocks.
"""
