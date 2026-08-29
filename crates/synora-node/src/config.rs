use synora_core::{
    consensus::{Validator, ValidatorId, ValidatorSet},
    state::Address,
};

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct NodeConfig {
    pub chain_id: u64,
    pub fee_recipient: Address,
    pub mempool_capacity: usize,
    pub block_gas_limit: u64,

    /// Validator set used by the node's consensus engine.
    pub validator_set: ValidatorSet,

    /// Validator identity operated by this node.
    pub validator_id: ValidatorId,
}

impl NodeConfig {
    pub fn new(
        chain_id: u64,
        fee_recipient: Address,
        mempool_capacity: usize,
        block_gas_limit: u64,
    ) -> Self {
        let validator_id = [1u8; 20];

        let validator_set = ValidatorSet::new(vec![
            Validator::new([1u8; 20]),
            Validator::new([2u8; 20]),
            Validator::new([3u8; 20]),
        ])
        .expect("default validator set must be valid");

        Self {
            chain_id,
            fee_recipient,
            mempool_capacity,
            block_gas_limit,
            validator_set,
            validator_id,
        }
    }

    /// Creates a node configuration with an explicit validator set
    /// and validator identity.
    pub fn with_consensus(
        chain_id: u64,
        fee_recipient: Address,
        mempool_capacity: usize,
        block_gas_limit: u64,
        validator_set: ValidatorSet,
        validator_id: ValidatorId,
    ) -> Self {
        assert!(
            validator_set.contains(&validator_id),
            "validator_id must exist in validator_set"
        );

        Self {
            chain_id,
            fee_recipient,
            mempool_capacity,
            block_gas_limit,
            validator_set,
            validator_id,
        }
    }

    #[cfg(test)]
    pub fn devnet() -> Self {
        let validator_a = [1u8; 20];
        let validator_b = [2u8; 20];
        let validator_c = [3u8; 20];

        let validator_set = ValidatorSet::new(vec![
            Validator::new(validator_a),
            Validator::new(validator_b),
            Validator::new(validator_c),
        ])
        .expect("devnet validator set should be valid");

        Self {
            chain_id: 1337,
            fee_recipient: [0xFE; 20],
            mempool_capacity: 10_000,
            block_gas_limit: 30_000_000,
            validator_set,
            validator_id: validator_a,
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

        assert_eq!(config.validator_set.len(), 3);
        assert_eq!(config.validator_set.total_power(), 3);
        assert_eq!(config.validator_id, [1u8; 20]);
    }

    #[test]
    fn custom_config_is_preserved() {
        let recipient = [7u8; 20];

        let config = NodeConfig::new(42, recipient, 100, 1_000_000);

        assert_eq!(config.chain_id, 42);
        assert_eq!(config.fee_recipient, recipient);
        assert_eq!(config.mempool_capacity, 100);
        assert_eq!(config.block_gas_limit, 1_000_000);

        assert_eq!(config.validator_set.len(), 3);
        assert_eq!(config.validator_set.total_power(), 3);
        assert_eq!(config.validator_id, [1u8; 20]);
    }

    #[test]
    fn explicit_consensus_config_is_preserved() {
        let validator_a = [10u8; 20];
        let validator_b = [11u8; 20];

        let validator_set = ValidatorSet::new(vec![
            Validator::with_power(validator_a, 2),
            Validator::with_power(validator_b, 3),
        ])
        .expect("validator set should be valid");

        let config = NodeConfig::with_consensus(
            9000,
            [0xAA; 20],
            500,
            2_000_000,
            validator_set.clone(),
            validator_b,
        );

        assert_eq!(config.chain_id, 9000);
        assert_eq!(config.fee_recipient, [0xAA; 20]);
        assert_eq!(config.mempool_capacity, 500);
        assert_eq!(config.block_gas_limit, 2_000_000);
        assert_eq!(config.validator_set, validator_set);
        assert_eq!(config.validator_id, validator_b);
    }

    #[test]
    #[should_panic(expected = "validator_id must exist in validator_set")]
    fn explicit_consensus_config_rejects_unknown_validator() {
        let validator_set = ValidatorSet::new(vec![Validator::new([1u8; 20])])
            .expect("validator set should be valid");

        NodeConfig::with_consensus(9000, [0xAA; 20], 500, 2_000_000, validator_set, [9u8; 20]);
    }
}
