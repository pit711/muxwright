################################################################################
# dablab-agent
################################################################################

DABLAB_AGENT_VERSION = local
DABLAB_AGENT_SITE = $(BR2_EXTERNAL_DABLAB_PATH)/../agent
DABLAB_AGENT_SITE_METHOD = local
DABLAB_AGENT_LICENSE = MIT
DABLAB_AGENT_DEPENDENCIES = libiio libad9361-iio

define DABLAB_AGENT_BUILD_CMDS
	$(TARGET_MAKE_ENV) $(MAKE) -C $(@D) CC="$(TARGET_CC)" CFLAGS="$(TARGET_CFLAGS) -O2" LDFLAGS="$(TARGET_LDFLAGS)"
endef

define DABLAB_AGENT_INSTALL_TARGET_CMDS
	$(INSTALL) -D -m 0755 $(@D)/dablab-agent $(TARGET_DIR)/usr/bin/dablab-agent
endef

$(eval $(generic-package))
