SHELL := /bin/sh
.DEFAULT_GOAL := all
.NOTPARALLEL:

SITE_CONFIG ?= config/site.yaml
ENV_FILE ?= .env
GUIDE_MODE ?= auto
REMOTE_HOST ?=
REMOTE_DIR ?=
REMOTE_PORT ?=
GUIDE_DEST ?= $(HOME)/Downloads/zelab-guide.html
BACKUP_KEY_FILE ?=
RESTORE_ARCHIVE ?=
CONFIRM ?=
export SITE_CONFIG ENV_FILE REMOTE_HOST REMOTE_DIR REMOTE_PORT GUIDE_DEST

INPUTS = --site-config "$(SITE_CONFIG)" --env-file "$(ENV_FILE)"
MAINTENANCE = python3 deploy/maintenance.py

.PHONY: all clean fclean re help backup restore bot notes

all:
	@$(if $(strip $(REMOTE_HOST)),python3 deploy/remote.py,python3 deploy/install.py $(INPUTS) --guide-mode "$(GUIDE_MODE)")

clean:
	@$(MAINTENANCE) clean $(INPUTS)

fclean:
	@$(MAINTENANCE) fclean $(INPUTS) --confirm "$(CONFIRM)"

# The reset finishes before installation, including with make -j.
re: fclean
	@$(MAKE) --no-print-directory

help:
	@python3 deploy/commands.py

# Terminal only: Hermes' own sign-in and chat wizards need a person.
bot:
	@python3 deploy/bots.py menu $(INPUTS)

# One deliberate sync of the server vault with its private Git remote (docs/NOTES.md).
notes:
	@python3 deploy/notes.py $(INPUTS)

backup:
	@$(MAINTENANCE) backup $(INPUTS) $(if $(BACKUP_KEY_FILE),--key-file "$(BACKUP_KEY_FILE)",)

restore:
	@$(MAINTENANCE) restore $(INPUTS) --archive "$(RESTORE_ARCHIVE)" --confirm "$(CONFIRM)" $(if $(BACKUP_KEY_FILE),--key-file "$(BACKUP_KEY_FILE)",)
