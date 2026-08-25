use synora_core::state::Address;

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct NodeConfig {
    pub chain_id: u64,
    pub fee_recipient: Address,
    pub mempool_capacity: usize,
    pub block_gas_limit: u64,
}

impl NodeConfig {
    pub fn new(
        chain_id: u64,
        fee_recipient: Address,
        mempool_capacity: usize,
        block_gas_limit: u64,
    ) -> Self {
        Self {
            chain_id,
            fee_recipient,
            mempool_capacity,
            block_gas_limit,
        }
    }

    #[cfg(test)]
    pub fn devnet() -> Self {
        Self {
            chain_id: 1337,
            fee_recipient: [0xFE; 20],
            mempool_capacity: 10_000,
            block_gas_limit: 30_000_000,
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn devnet_config_is_valid() {
        let config = NodeConfig::devnet();

        assert_eq!(config.chain_id, 1337);
        assert_eq!(config.fee_recipient, [0xFE; 20]);
        assert_eq!(config.mempool_capacity, 10_000);
        assert_eq!(config.block_gas_limit, 30_000_000);
    }

    #[test]
    fn custom_config_is_preserved() {
        let recipient = [7u8; 20];

        let config = NodeConfig::new(42, recipient, 100, 1_000_000);

        assert_eq!(config.chain_id, 42);
        assert_eq!(config.fee_recipient, recipient);
        assert_eq!(config.mempool_capacity, 100);
        assert_eq!(config.block_gas_limit, 1_000_000);
    }
}
