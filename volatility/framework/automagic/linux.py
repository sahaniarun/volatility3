import logging
import os
import pickle

from volatility.framework import interfaces, constants
from volatility.framework.layers import intel, scanners

vollog = logging.getLogger(__name__)


class LinuxSymbolFinder(interfaces.automagic.AutomagicInterface):
    priority = 20

    def __init__(self, context, config_path):
        super().__init__(context, config_path)
        self._requirements = None
        self._linux_banners = {}
        self._load_linux_banners()

    def __call__(self, context, config_path, requirement, progress_callback = None):
        """Searches for LinuxSymbolRequirements and attempt to populate them"""
        self._requirements = self.find_requirements(context, config_path, requirement,
                                                    (interfaces.configuration.TranslationLayerRequirement,
                                                     interfaces.configuration.SymbolRequirement),
                                                    shortcut = False)

        for (path, sub_path, requirement) in self._requirements:
            if isinstance(requirement, interfaces.configuration.SymbolRequirement):
                for (tl_path, tl_sub_path, tl_requirement) in self._requirements:
                    # Find the TranslationLayer sibling to the SymbolRequirement
                    if (isinstance(tl_requirement, interfaces.configuration.TranslationLayerRequirement) and
                                tl_path == path):
                        # TODO: Find the physical layer properly, not just for Intel
                        physical_path = interfaces.configuration.path_join(tl_sub_path, "memory_layer")
                        self._banner_scan(context, path, requirement, context.config[physical_path],
                                          progress_callback)

    def _load_linux_banners(self):
        if os.path.exists(constants.LINUX_BANNERS_PATH):
            with open(constants.LINUX_BANNERS_PATH, "rb") as f:
                # We use pickle over JSON because we're dealing with bytes objects
                self._linux_banners.update(pickle.load(f))

    def _banner_scan(self, context, config_path, requirement, layer_name, progress_callback = None):
        """Accepts a context, config_path and SymbolRequirement, with a constructed layer_name
        and scans the layer for linux banners"""
        mss = scanners.MultiStringScanner(list(self._linux_banners))

        layer = context.memory[layer_name]

        for offset, banner in layer.scan(context = context, scanner = mss, progress_callback = progress_callback):
            vollog.debug("Identified banner: {}".format(repr(banner)))
            symbol_files = self._linux_banners[banner]
            isf_path = "file://" + symbol_files[0]
            if isf_path:
                vollog.debug("Using symbol library: {}".format(symbol_files[0]))
                clazz = "volatility.framework.symbols.linux.LinuxKernelIntermedSymbols"
                # Set the discovered options
                path_join = interfaces.configuration.path_join
                context.config[path_join(config_path, requirement.name, "class")] = clazz
                context.config[path_join(config_path, requirement.name, "isf_filepath")] = isf_path
                # Construct the appropriate symbol table
                requirement.construct(context, config_path)
                break
            else:
                vollog.debug("Symbol library path not found: {}".format(symbol_files[0]))
                # print("Kernel", banner, hex(banner_offset))
        else:
            pass


class LintelStacker(interfaces.automagic.StackerLayerInterface):
    linux_signature = b"SYMBOL\(swapper_pg_dir\)=.*"
    stack_order = 9

    @classmethod
    def stack(cls, context, layer_name, progress_callback = None):
        """Attempts to identify linux within this layer"""
        layer = context.memory[layer_name]

        # Bail out if we're not a physical layer
        # TODO: We need a better way of doing this
        if isinstance(layer, intel.Intel):
            return None

        swapper_pg_dirs = []
        for offset in layer.scan(scanner = scanners.RegExScanner(cls.linux_signature), context = context):
            swapper_pg_dir_text = context.memory[layer_name].read(offset, len(cls.linux_signature) + 20)
            swapper_pg_dir = int(swapper_pg_dir_text[
                                 swapper_pg_dir_text.index(b"=") + 1:swapper_pg_dir_text.index(b"\n")], 16)
            swapper_pg_dirs.append(swapper_pg_dir)

        best_swapper_pg_dir = list(reversed(sorted(set(swapper_pg_dirs), key = lambda x: swapper_pg_dirs.count(x))))[0]