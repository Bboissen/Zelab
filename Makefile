SHELL := /bin/sh
.DEFAULT_GOAL := all
.NOTPARALLEL:

RTK_BIN_DIR ?= $(HOME)/.local/bin
export PATH := $(RTK_BIN_DIR):$(PATH)
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
MAINTENANCE = rtk proxy python3 deploy/maintenance.py

.PHONY: all clean fclean re help backup restore bot

all:
	@$(if $(strip $(REMOTE_HOST)),rtk proxy python3 deploy/remote.py,rtk proxy python3 deploy/install.py $(INPUTS) --guide-mode "$(GUIDE_MODE)")

clean:
	@$(MAINTENANCE) clean $(INPUTS)

fclean:
	@$(MAINTENANCE) fclean $(INPUTS) --confirm "$(CONFIRM)"

# The reset finishes before installation, including with make -j.
re: fclean
	@rtk proxy $(MAKE) --no-print-directory

help:
	@rtk proxy python3 deploy/commands.py

# Terminal only: Hermes' own sign-in and chat wizards need a person.
bot:
	@rtk proxy python3 deploy/bots.py menu $(INPUTS)

backup:
	@$(MAINTENANCE) backup $(INPUTS) $(if $(BACKUP_KEY_FILE),--key-file "$(BACKUP_KEY_FILE)",)

restore:
	@$(MAINTENANCE) restore $(INPUTS) --archive "$(RESTORE_ARCHIVE)" --confirm "$(CONFIRM)" $(if $(BACKUP_KEY_FILE),--key-file "$(BACKUP_KEY_FILE)",)
