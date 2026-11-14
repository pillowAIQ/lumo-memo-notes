import sys

if len(sys.argv) == 3 and sys.argv[1] == "--verify-package":
    from memo_app.packaging_smoke import verify
    sys.exit(verify(sys.argv[2]))
else:
    from memo_app.__main__ import main
    main()
