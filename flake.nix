{
  description = "Chongming development environment and virtual-scene tools";

  inputs = {
    # This revision provides the required Godot 4.7.2 stable, not an arbitrary Godot 4.
    nixpkgs.url = "github:NixOS/nixpkgs/fe13242281a67641c0102cdb1087f0ebe586c7df";

    pyproject-nix = {
      url = "github:pyproject-nix/pyproject.nix";
      inputs.nixpkgs.follows = "nixpkgs";
    };
    uv2nix = {
      url = "github:pyproject-nix/uv2nix";
      inputs.nixpkgs.follows = "nixpkgs";
      inputs.pyproject-nix.follows = "pyproject-nix";
    };
    pyproject-build-systems = {
      url = "github:pyproject-nix/build-system-pkgs";
      inputs.nixpkgs.follows = "nixpkgs";
      inputs.pyproject-nix.follows = "pyproject-nix";
      inputs.uv2nix.follows = "uv2nix";
    };
  };

  outputs =
    {
      self,
      nixpkgs,
      pyproject-nix,
      uv2nix,
      pyproject-build-systems,
      ...
    }:
    let
      inherit (nixpkgs) lib;
      # These are the platforms supported by the pinned Godot package.
      systems = [
        "x86_64-linux"
        "aarch64-linux"
        "aarch64-darwin"
      ];
      forAllSystems = lib.genAttrs systems;
      workspace = uv2nix.lib.workspace.loadWorkspace { workspaceRoot = ./.; };
      overlay = workspace.mkPyprojectOverlay { sourcePreference = "wheel"; };

      environments = forAllSystems (
        system:
        let
          pkgs = nixpkgs.legacyPackages.${system};
          pythonSet =
            (pkgs.callPackage pyproject-nix.build.packages { python = pkgs.python312; }).overrideScope
              (
                lib.composeManyExtensions [
                  pyproject-build-systems.overlays.wheel
                  overlay
                ]
              );
          editableSet = pythonSet.overrideScope (
            lib.composeManyExtensions [
              (workspace.mkEditablePyprojectOverlay { root = "$REPO_ROOT"; })
              (final: prev: {
                chongming = prev.chongming.overrideAttrs (old: {
                  # Hatchling imports editables when producing a PEP 660 wheel.
                  nativeBuildInputs = old.nativeBuildInputs ++ final.resolveBuildSystem { editables = [ ]; };
                });
              })
            ]
          );
          godot =
            assert lib.assertMsg (
              pkgs.godot_4.version == "4.7.2-stable"
            ) "Chongming requires Godot 4.7.2 stable; do not change it without approval.";
            pkgs.godot_4;
        in
        {
          inherit pkgs godot;
          runtime = pythonSet.mkVirtualEnv "chongming-runtime" workspace.deps.default;
          tools = pythonSet.mkVirtualEnv "chongming-tools" workspace.deps.all;
          editable = editableSet.mkVirtualEnv "chongming-dev" workspace.deps.all;
        }
      );
    in
    {
      packages = forAllSystems (
        system:
        let
          inherit (environments.${system})
            pkgs
            godot
            runtime
            tools
            ;
          application = pkgs.symlinkJoin {
            name = "chongming";
            paths = [ runtime ];
            nativeBuildInputs = [ pkgs.makeWrapper ];
            postBuild = ''
              for command in chongming-preview chongming-center chongming-scene; do
                wrapProgram "$out/bin/$command" \
                  --set CHONGMING_GODOT ${lib.getExe godot} \
                  --prefix PATH : ${lib.makeBinPath [ pkgs.git ]}
              done
            '';
            meta.mainProgram = "chongming-preview";
          };
        in
        {
          default = application;
          chongming = application;
          inherit godot;
          python-env = runtime;
          dev-tools = tools;
        }
      );

      apps = forAllSystems (
        system:
        let
          application = self.packages.${system}.chongming;
          app = command: {
            type = "app";
            program = "${application}/bin/chongming-${command}";
            meta.description = "Run the Chongming ${command} tool";
          };
        in
        {
          default = app "preview";
          preview = app "preview";
          center = app "center";
          scene = app "scene";
        }
      );

      devShells = forAllSystems (
        system:
        let
          inherit (environments.${system}) pkgs godot editable;
          mkDevShell =
            extra:
            pkgs.mkShell (
              {
                packages = [
                  editable
                  godot
                  pkgs.uv
                  pkgs.git
                  pkgs.nixfmt
                ]
                ++ lib.optionals pkgs.stdenv.hostPlatform.isLinux [ pkgs.xvfb-run ];
                CHONGMING_GODOT = lib.getExe godot;
                UV_NO_SYNC = "1";
                UV_FROZEN = "1";
                UV_PYTHON = "${editable}/bin/python";
                UV_PYTHON_DOWNLOADS = "never";
                UV_PROJECT_ENVIRONMENT = "${editable}";
                shellHook = ''
                  unset PYTHONPATH
                  export REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
                  if [ ! -f "$REPO_ROOT/pyproject.toml" ]; then
                    echo "Enter the Chongming repository before running nix develop." >&2
                    return 1
                  fi
                  export VIRTUAL_ENV="${editable}"
                '';
              }
              // extra
            );
        in
        {
          default = mkDevShell { };
        }
        // lib.optionalAttrs pkgs.stdenv.hostPlatform.isLinux {
          # A real OpenGL context using Mesa/llvmpipe, not Godot's dummy renderer.
          software = mkDevShell {
            LIBGL_ALWAYS_SOFTWARE = "1";
            LIBGL_DRIVERS_PATH = "${pkgs.mesa}/lib/dri";
            LD_LIBRARY_PATH = lib.makeLibraryPath [ pkgs.mesa ];
            __EGL_VENDOR_LIBRARY_FILENAMES = "${pkgs.mesa}/share/glvnd/egl_vendor.d/50_mesa.json";
            __GLX_VENDOR_LIBRARY_NAME = "mesa";
          };
        }
      );

      formatter = forAllSystems (
        system:
        let
          pkgs = nixpkgs.legacyPackages.${system};
        in
        pkgs.writeShellApplication {
          name = "chongming-nixfmt";
          runtimeInputs = [ pkgs.nixfmt ];
          text = ''
            if [ "$#" -eq 0 ]; then
              set -- flake.nix
            fi
            exec nixfmt "$@"
          '';
        }
      );

      # Display-dependent integration tests are run explicitly in nix develop.
      checks = forAllSystems (
        system:
        let
          inherit (environments.${system}) pkgs tools;
          sourceCheck =
            name: command:
            pkgs.runCommand name { nativeBuildInputs = [ tools ]; } ''
              export HOME="$TMPDIR"
              export PYTHONDONTWRITEBYTECODE=1
              cp -R ${self} source
              chmod -R u+w source
              cd source
              ${command}
              touch "$out"
            '';
        in
        {
          lint = sourceCheck "chongming-lint" "ruff check .";
          format = sourceCheck "chongming-format" "ruff format --check src tests";
          types = sourceCheck "chongming-types" "mypy --cache-dir=$TMPDIR/mypy src tests";
          architecture = sourceCheck "chongming-architecture" "python -m unittest tests.test_architecture -v";
          environment = sourceCheck "chongming-environment" ''
            export CHONGMING_GODOT=${lib.getExe environments.${system}.godot}
            python nix/check-environment.py
          '';
          scenarios = sourceCheck "chongming-scenarios" ''
            for scenario in scenarios/*.json; do
              chongming-center --scenario "$scenario" --check
            done
          '';
          nix-format = pkgs.runCommand "chongming-nix-format" { nativeBuildInputs = [ pkgs.nixfmt ]; } ''
            nixfmt --check ${./flake.nix}
            touch "$out"
          '';
        }
      );
    };
}
