################################################################################
# iqplay
################################################################################

IQPLAY_VERSION = local
IQPLAY_SITE = $(BR2_EXTERNAL_DABLAB_PATH)/../iqplay
IQPLAY_SITE_METHOD = local
IQPLAY_LICENSE = MIT
IQPLAY_DEPENDENCIES = libiio libad9361-iio

define IQPLAY_BUILD_CMDS
	$(TARGET_MAKE_ENV) $(MAKE) -C $(@D) CC="$(TARGET_CC)" CFLAGS="$(TARGET_CFLAGS) -O2" LDFLAGS="$(TARGET_LDFLAGS)"
endef

define IQPLAY_INSTALL_TARGET_CMDS
	$(INSTALL) -D -m 0755 $(@D)/iqplay $(TARGET_DIR)/usr/bin/iqplay
endef

$(eval $(generic-package))
