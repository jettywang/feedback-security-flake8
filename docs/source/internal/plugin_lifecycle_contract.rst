Plugin Lifecycle and the ``--help`` Contract
============================================

This document records the *observed* order of plugin discovery, import,
classification and option registration when :class:`~flake8.main.application.
Application` runs, and the difference between a normal check and ``--help``.
The behaviour is pinned by the contract tests in
``tests/integration/test_plugin_lifecycle_contract.py`` so that a future review
or audit service which restricts plugin loading has a stable baseline.

Entry point from the command line
----------------------------------

``flake8 ...`` calls :func:`flake8.main.cli.main`, which constructs an
:class:`~flake8.main.application.Application` and calls ``run`` -> ``_run`` ->
:meth:`~flake8.main.application.Application.initialize`.  ``initialize`` calls
:func:`flake8.options.parse_args.parse_args`, where the whole plugin pipeline
lives::

    Application.initialize(argv)
    `-- parse_args(argv)
        |-- stage1_arg_parser().parse_known_args(argv)   # preliminary opts
        |-- config.load_config(...)                       # merge config files
        |-- finder.parse_plugin_options(...)              # local paths,
        |                                                 # --enable-extensions
        |-- finder.find_plugins(cfg, opts)                # 1. DISCOVERY
        |-- finder.load_plugins(raw_plugins, opts)        # 2. IMPORT + 3. CLASSIFY
        |-- OptionManager(...)
        |-- options.register_default_options(...)
        |-- option_manager.register_plugins(plugins)      # 4. REGISTRATION
        `-- aggregator.aggregate_options(...)             # argparse runs here

``--help`` is **not** a stage-1 option (the preliminary parser is created with
``add_help=False``).  It stays in the unparsed remainder and only triggers
argparse's help action inside ``aggregate_options``, i.e. *after* every plugin
has been discovered, imported, classified and registered.

1. Discovery (``finder.find_plugins``)
--------------------------------------

#. ``_find_importlib_plugins`` scans ``importlib.metadata.distributions()`` and
   collects every entry point in the ``flake8.extension`` and ``flake8.report``
   groups.  The ``flake8`` distribution's ``F`` / ``E`` / ``W`` entry points are
   re-attributed to the ``pyflakes`` and ``pycodestyle`` packages; obsolete
   packages in ``BANNED_PLUGINS`` are skipped.
#. ``_find_local_plugins`` reads the ``[flake8:local-plugins]`` config section
   (``extension`` / ``report``) and synthesises ``EntryPoint`` objects tagged
   with the package name ``local``.
#. The combined list is sorted for determinism and ``require_plugins`` is
   validated.

At this stage nothing is imported: ``off_by_default`` plugins and plugins that
will later be disabled are present in the returned list exactly like the
enabled ones.

2. Import (``finder._import_plugins``)
--------------------------------------

#. ``sys.path`` is extended with the local-plugin ``paths``.
#. Every discovered plugin's ``EntryPoint.load()`` is executed in sorted order,
   producing a ``LoadedPlugin`` whose ``parameters`` are inspected.

Import is unconditional.  A plugin marked ``off_by_default`` is still imported,
and an unloadable plugin raises ``FailedToLoadPlugin`` here -- even for
``flake8 --help``.  Help output can therefore never mask a broken installation.

3. Classification (``finder._classify_plugins``)
------------------------------------------------

Each loaded plugin is placed in exactly one bucket:

* ``disabled`` -- ``obj.off_by_default`` is true **and** its entry name is not
  in ``--enable-extensions``;
* ``reporters`` -- entry point group ``flake8.report``;
* ``checkers.tree`` / ``checkers.logical_line`` /
  ``checkers.physical_line`` -- according to the plugin callable's parameters.

Checker entry names are then validated against ``VALID_CODE_PREFIX``.

4. Registration (``OptionManager.register_plugins``)
----------------------------------------------------

Only ``plugins.all_plugins()`` is iterated, which yields the three checker
buckets and the reporters -- never ``plugins.disabled``.  For each enabled
plugin:

* its ``add_options`` classmethod (if present) registers a dedicated option
  group;
* checker entry names are appended to ``extended_default_select``;
* reporter names feed the ``--format`` choice list shown in ``--help``.

After argument aggregation, each enabled plugin's ``parse_options`` hook is
called.  Disabled plugins receive neither ``add_options`` nor
``parse_options`` calls.

Enabled vs. disabled configuration
----------------------------------

Using a local extension ``LD`` with ``off_by_default = True`` and a normal
extension ``LE``:

==============================  ======================================  ======================================
Behaviour                       LD disabled (default)                   LD enabled (``--enable-extensions=LD``)
==============================  ======================================  ======================================
Discovered / imported           yes                                     yes
Bucket after classification     ``Plugins.disabled``                    ``checkers.tree``
``add_options`` / option group  not registered                          registered
In ``extended_default_select``  no                                      yes
``parse_options`` hook          not called                              called
Executed during a normal check  no                                      yes (reports its code)
Option usable on the CLI        rejected as unrecognized (exit code 2)  accepted
==============================  ======================================  ======================================

Normal check vs. ``--help``
---------------------------

The two invocations share the entire pipeline up to and including
registration.  They only diverge at the final argparse step:

* ``flake8 sample.py`` -- aggregates options, then ``Application`` builds the
  formatter, style guide and checker manager and runs the enabled checkers;
* ``flake8 --help`` -- argparse prints the usage text (including the
  ``Installed plugins:`` epilog built from ``versions_str`` and the
  ``--format`` formatter list) and raises ``SystemExit(0)`` before any
  formatter, guide or checker is constructed.

Consequences a review-service policy must rely on
--------------------------------------------------

* Blocking a plugin at *discovery* removes it from import, registration, help
  and checking in one place.
* Blocking at *import* still prevents the run and also blocks ``--help``; the
  failure mode is ``FailedToLoadPlugin``, indistinguishable from a genuinely
  broken plugin.
* Merely marking a plugin ``off_by_default`` hides its options and checks but
  does **not** prevent its module code from being imported, so import-time side
  effects still execute on every invocation, including ``--help``.
* ``--help`` is an integrity check of the full plugin set, not a cheap
  plugin-free path.
