################################################################################
# dablab-mod – Modulatoren aus pluto_fw/mod (dvbmod_lab: dvbt_mod, dvbc_mod,
# rrc_fir_gen, dvbmod_selftest) mit lokalem IIO-Kontext (HAVE_LIBIIO=1),
# NEON, -O3. Programme landen in /usr/bin.
################################################################################

DABLAB_MOD_VERSION = local
DABLAB_MOD_SITE = $(BR2_EXTERNAL_DABLAB_PATH)/../mod
DABLAB_MOD_SITE_METHOD = local
DABLAB_MOD_LICENSE = siehe mod/LICENSE
DABLAB_MOD_DEPENDENCIES = libiio host-pkgconf

DABLAB_MOD_CFLAGS = $(TARGET_CFLAGS) -O3 -mcpu=cortex-a9 -mfpu=neon -mfloat-abi=hard
DABLAB_MOD_PROGS = dvbt_mod dvbc_mod rrc_fir_gen dvbmod_selftest

define DABLAB_MOD_BUILD_CMDS
	$(TARGET_MAKE_ENV) $(MAKE) -C $(@D) clean
	# CFLAGS/LDFLAGS als Umgebung (nicht als make-Argument), sonst überschreiben sie "CFLAGS += -DHAVE_LIBIIO" im Makefile
	$(TARGET_MAKE_ENV) CFLAGS="$(DABLAB_MOD_CFLAGS)" LDFLAGS="$(TARGET_LDFLAGS)" \
		$(MAKE) -C $(@D) CC="$(TARGET_CC)" AR="$(TARGET_AR)" HAVE_LIBIIO=1 $(DABLAB_MOD_PROGS)
	# Absicherung: ohne IIO-Backend wäre der Modulator auf dem Pluto nutzlos
	if grep -q "ohne HAVE_LIBIIO gebaut" $(@D)/dvbt_mod; then echo "dablab-mod: dvbt_mod ohne HAVE_LIBIIO gebaut" >&2; exit 1; fi
endef

define DABLAB_MOD_INSTALL_TARGET_CMDS
	for p in $(DABLAB_MOD_PROGS); do $(INSTALL) -D -m 0755 $(@D)/$$p $(TARGET_DIR)/usr/bin/$$p || exit 1; done
endef

$(eval $(generic-package))
