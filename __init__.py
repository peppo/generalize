def classFactory(iface):
    from .generalize_plugin import GeneralizePlugin
    return GeneralizePlugin(iface)