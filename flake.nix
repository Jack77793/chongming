{
  description = "Chongming development environment and virtual-scene tools";

  inputs.nixpkgs.url = "https://channels.nixos.org/nixos-unstable/nixexprs.tar.zst";

  outputs =
    { nixpkgs, ... }:
    let
      inherit (nixpkgs) lib;

      systems = [
        "x86_64-linux"
        "aarch64-linux"
        "aarch64-darwin"
      ];

      pkgsFor = system: nixpkgs.legacyPackages.${system};

      runtimeEnv = pkgs: {
        CHONGMING_GODOT = lib.getExe pkgs.godot_4_7;
        CHONGMING_FFMPEG = lib.getExe pkgs.ffmpeg;
        LD_LIBRARY_PATH = lib.makeLibraryPath [
          pkgs.stdenv.cc.cc.lib
          pkgs.zlib
        ];
        UV_PYTHON_DOWNLOADS = "never";
      };

      treefmtFor =
        pkgs:
        pkgs.writeShellApplication {
          name = "treefmt";
          runtimeInputs = [
            pkgs.nixfmt
            pkgs.prettier
            pkgs.ruff
            pkgs.git
          ];
          text = ''
            exec ${lib.getExe pkgs.treefmt} --config-file ${./treefmt.toml} "$@"
          '';
          meta.description = "Format the project with nixfmt, Ruff and Prettier";
        };

      appsFor =
        system:
        let
          pkgs = pkgsFor system;
          preview = {
            type = "app";
            program = lib.getExe (
              pkgs.writeShellApplication {
                name = "chongming-preview";
                runtimeInputs = [
                  pkgs.python312
                  pkgs.uv
                  pkgs.godot_4_7
                  pkgs.ffmpeg
                ];
                runtimeEnv = runtimeEnv pkgs;
                text = ''
                  exec uv run --frozen chongming-preview "$@"
                '';
              }
            );
          };
        in
        {
          default = preview;
          preview = preview;
        };

      devShellsFor =
        system:
        let
          pkgs = pkgsFor system;
          treefmt = treefmtFor pkgs;

          shellFor =
            extra:
            pkgs.mkShell (
              {
                packages = [
                  pkgs.python312
                  pkgs.uv
                  pkgs.godot_4_7
                  pkgs.ffmpeg
                  pkgs.ruff
                  pkgs.git
                  treefmt
                ]
                ++ lib.optional pkgs.stdenv.hostPlatform.isLinux pkgs.xvfb-run;
                shellHook = ''
                  echo "Python deps come from uv: run 'uv run chongming-center ...' from the repository root." >&2
                '';
              }
              // runtimeEnv pkgs
              // extra
            );
        in
        {
          default = shellFor { };
        }
        // lib.optionalAttrs pkgs.stdenv.hostPlatform.isLinux {
          software = shellFor {
            LD_LIBRARY_PATH = lib.makeLibraryPath [
              pkgs.mesa
              pkgs.stdenv.cc.cc.lib
              pkgs.zlib
            ];
            LIBGL_ALWAYS_SOFTWARE = "1";
            LIBGL_DRIVERS_PATH = "${pkgs.mesa}/lib/dri";
            __EGL_VENDOR_LIBRARY_FILENAMES = "${pkgs.mesa}/share/glvnd/egl_vendor.d/50_mesa.json";
            __GLX_VENDOR_LIBRARY_NAME = "mesa";
          };
        };
    in
    {
      apps = lib.genAttrs systems appsFor;
      devShells = lib.genAttrs systems devShellsFor;
      formatter = lib.genAttrs systems (system: treefmtFor (pkgsFor system));
    };
}
