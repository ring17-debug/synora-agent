//! Synora consensus primitives.
//!
//! This module intentionally contains only deterministic consensus
//! primitives. Networking, signatures, vote propagation, and persistent
//! consensus state belong to higher layers.
//!
//! Current model:
//! - validator set with unique validator addresses
//! - deterministic round-robin proposer selection
//! - Byzantine-style two-thirds quorum calculation
//! - block proposer validation
//!
//! The implementation is deliberately small so it can later be extended
//! into a full BFT consensus protocol without coupling consensus logic to
//! the node or RPC layer.

use crate::block::Block;
use crate::hash::Hash;

pub const CONSENSUS_VERSION: u8 = 1;

pub type ValidatorId = [u8; 20];

/// A validator participating in consensus.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Validator {
    pub id: ValidatorId,
    pub power: u64,
}

impl Validator {
    /// Creates a validator with one unit of voting power.
    pub fn new(id: ValidatorId) -> Self {
        Self { id, power: 1 }
    }

    /// Creates a validator with explicit voting power.
    pub fn with_power(id: ValidatorId, power: u64) -> Self {
        Self { id, power }
    }

    pub fn is_valid(&self) -> bool {
        self.power > 0
    }
}

/// A deterministic validator set.
///
/// Validators are kept in insertion order. This makes proposer selection
/// deterministic across nodes as long as every node has the same validator
/// set.
#[derive(Debug, Clone, PartialEq, Eq, Default)]
pub struct ValidatorSet {
    validators: Vec<Validator>,
}

impl ValidatorSet {
    pub fn new(validators: Vec<Validator>) -> Result<Self, ConsensusError> {
        let mut set = Self::default();

        for validator in validators {
            set.add(validator)?;
        }

        Ok(set)
    }

    pub fn empty() -> Self {
        Self::default()
    }

    pub fn len(&self) -> usize {
        self.validators.len()
    }

    pub fn is_empty(&self) -> bool {
        self.validators.is_empty()
    }

    pub fn total_power(&self) -> u64 {
        self.validators
            .iter()
            .map(|validator| validator.power)
            .sum()
    }

    pub fn validators(&self) -> &[Validator] {
        &self.validators
    }

    pub fn contains(&self, id: &ValidatorId) -> bool {
        self.validators.iter().any(|validator| &validator.id == id)
    }

    pub fn get(&self, id: &ValidatorId) -> Option<&Validator> {
        self.validators.iter().find(|validator| &validator.id == id)
    }

    pub fn add(&mut self, validator: Validator) -> Result<(), ConsensusError> {
        if !validator.is_valid() {
            return Err(ConsensusError::InvalidVotingPower);
        }

        if self.contains(&validator.id) {
            return Err(ConsensusError::DuplicateValidator);
        }

        self.validators.push(validator);

        Ok(())
    }

    pub fn remove(&mut self, id: &ValidatorId) -> Result<Validator, ConsensusError> {
        let index = self
            .validators
            .iter()
            .position(|validator| &validator.id == id)
            .ok_or(ConsensusError::ValidatorNotFound)?;

        Ok(self.validators.remove(index))
    }

    /// Selects the deterministic proposer for a block height and round.
    ///
    /// Formula:
    ///
    /// ```text
    /// (height + round) % validator_count
    /// ```
    ///
    /// Genesis height 0 is therefore also deterministic, although consensus
    /// should normally start proposing from height 1.
    pub fn proposer(&self, height: u64, round: u64) -> Result<&Validator, ConsensusError> {
        if self.validators.is_empty() {
            return Err(ConsensusError::EmptyValidatorSet);
        }

        let index = ((height as usize) + (round as usize)) % self.validators.len();

        Ok(&self.validators[index])
    }

    /// Returns whether the supplied voting power reaches the Byzantine
    /// two-thirds quorum.
    ///
    /// The comparison avoids floating point arithmetic:
    ///
    /// ```text
    /// power * 3 >= total_power * 2
    /// ```
    pub fn has_quorum(&self, voting_power: u64) -> bool {
        let total = self.total_power();

        if total == 0 || voting_power > total {
            return false;
        }

        voting_power.saturating_mul(3) >= total.saturating_mul(2)
    }

    /// Returns the minimum voting power required for quorum.
    ///
    /// This is the mathematical ceiling of 2/3 of the total power.
    pub fn quorum_power(&self) -> u64 {
        let total = self.total_power();

        if total == 0 {
            return 0;
        }

        total.saturating_mul(2).saturating_add(2) / 3
    }
}

/// Consensus round identifier.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub struct ConsensusRound {
    pub height: u64,
    pub round: u64,
}

impl ConsensusRound {
    pub fn new(height: u64, round: u64) -> Self {
        Self { height, round }
    }
}

/// Basic block proposal metadata.
///
/// This is deliberately separate from `Block` so the consensus layer can
/// validate proposal ownership without changing the block format yet.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct BlockProposal {
    pub block_hash: Hash,
    pub height: u64,
    pub round: u64,
    pub proposer: ValidatorId,
}

impl BlockProposal {
    pub fn new(block: &Block, round: u64, proposer: ValidatorId) -> Self {
        Self {
            block_hash: block.hash(),
            height: block.header.height,
            round,
            proposer,
        }
    }
}

/// Validates a block proposal against a validator set.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ConsensusValidator {
    validator_set: ValidatorSet,
}

impl ConsensusValidator {
    pub fn new(validator_set: ValidatorSet) -> Self {
        Self { validator_set }
    }

    pub fn validator_set(&self) -> &ValidatorSet {
        &self.validator_set
    }

    /// Returns the expected proposer for the supplied height and round.
    pub fn expected_proposer(
        &self,
        height: u64,
        round: u64,
    ) -> Result<ValidatorId, ConsensusError> {
        Ok(self.validator_set.proposer(height, round)?.id)
    }

    /// Validates that a proposal was created by the deterministic proposer.
    pub fn validate_proposer(
        &self,
        block: &Block,
        round: u64,
        proposer: &ValidatorId,
    ) -> Result<(), ConsensusError> {
        let expected = self.expected_proposer(block.header.height, round)?;

        if &expected != proposer {
            return Err(ConsensusError::InvalidProposer);
        }

        Ok(())
    }

    /// Validates the proposal's basic block/height relationship.
    pub fn validate_proposal(&self, proposal: &BlockProposal) -> Result<(), ConsensusError> {
        let expected = self.expected_proposer(proposal.height, proposal.round)?;

        if proposal.proposer != expected {
            return Err(ConsensusError::InvalidProposer);
        }

        Ok(())
    }
}

/// Errors produced by the consensus primitives.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum ConsensusError {
    EmptyValidatorSet,
    DuplicateValidator,
    ValidatorNotFound,
    InvalidVotingPower,
    InvalidProposer,
}

impl std::fmt::Display for ConsensusError {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        let message = match self {
            Self::EmptyValidatorSet => "validator set is empty",
            Self::DuplicateValidator => "validator already exists",
            Self::ValidatorNotFound => "validator not found",
            Self::InvalidVotingPower => "validator voting power must be greater than zero",
            Self::InvalidProposer => "proposal proposer is not the expected proposer",
        };

        formatter.write_str(message)
    }
}

impl std::error::Error for ConsensusError {}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::block::Block;

    fn validator(value: u8) -> Validator {
        Validator::new([value; 20])
    }

    #[test]
    fn validator_can_be_created() {
        let validator = validator(1);

        assert_eq!(validator.id, [1u8; 20]);
        assert_eq!(validator.power, 1);
        assert!(validator.is_valid());
    }

    #[test]
    fn zero_power_validator_is_invalid() {
        let validator = Validator::with_power([1u8; 20], 0);

        assert!(!validator.is_valid());

        let result = ValidatorSet::new(vec![validator]);

        assert_eq!(result, Err(ConsensusError::InvalidVotingPower));
    }

    #[test]
    fn validator_set_rejects_duplicates() {
        let result = ValidatorSet::new(vec![validator(1), validator(1)]);

        assert_eq!(result, Err(ConsensusError::DuplicateValidator));
    }

    #[test]
    fn validator_set_tracks_total_power() {
        let set = ValidatorSet::new(vec![
            Validator::with_power([1u8; 20], 2),
            Validator::with_power([2u8; 20], 3),
            Validator::with_power([3u8; 20], 5),
        ])
        .expect("validator set should be valid");

        assert_eq!(set.len(), 3);
        assert_eq!(set.total_power(), 10);
    }

    #[test]
    fn proposer_selection_is_deterministic() {
        let set = ValidatorSet::new(vec![validator(1), validator(2), validator(3)])
            .expect("validator set should be valid");

        assert_eq!(set.proposer(1, 0).unwrap().id, [2u8; 20]);
        assert_eq!(set.proposer(1, 1).unwrap().id, [3u8; 20]);
        assert_eq!(set.proposer(1, 2).unwrap().id, [1u8; 20]);
        assert_eq!(set.proposer(2, 0).unwrap().id, [3u8; 20]);
    }

    #[test]
    fn empty_validator_set_cannot_select_proposer() {
        let set = ValidatorSet::empty();

        assert_eq!(set.proposer(1, 0), Err(ConsensusError::EmptyValidatorSet));
    }

    #[test]
    fn quorum_requires_two_thirds() {
        let set = ValidatorSet::new(vec![validator(1), validator(2), validator(3)])
            .expect("validator set should be valid");

        assert_eq!(set.total_power(), 3);
        assert_eq!(set.quorum_power(), 2);

        assert!(!set.has_quorum(1));
        assert!(set.has_quorum(2));
        assert!(set.has_quorum(3));
    }

    #[test]
    fn weighted_quorum_is_correct() {
        let set = ValidatorSet::new(vec![
            Validator::with_power([1u8; 20], 1),
            Validator::with_power([2u8; 20], 2),
            Validator::with_power([3u8; 20], 6),
        ])
        .expect("validator set should be valid");

        assert_eq!(set.total_power(), 9);
        assert_eq!(set.quorum_power(), 6);

        assert!(!set.has_quorum(5));
        assert!(set.has_quorum(6));
        assert!(set.has_quorum(9));
    }

    #[test]
    fn voting_power_above_total_is_rejected() {
        let set = ValidatorSet::new(vec![validator(1), validator(2), validator(3)])
            .expect("validator set should be valid");

        assert!(!set.has_quorum(4));
    }

    #[test]
    fn validator_can_be_removed() {
        let mut set = ValidatorSet::new(vec![validator(1), validator(2)])
            .expect("validator set should be valid");

        let removed = set.remove(&[1u8; 20]).expect("validator should exist");

        assert_eq!(removed.id, [1u8; 20]);
        assert_eq!(set.len(), 1);
        assert!(!set.contains(&[1u8; 20]));
    }

    #[test]
    fn removing_unknown_validator_fails() {
        let mut set = ValidatorSet::new(vec![validator(1)]).expect("validator set should be valid");

        assert_eq!(
            set.remove(&[9u8; 20]),
            Err(ConsensusError::ValidatorNotFound)
        );
    }

    #[test]
    fn expected_proposer_is_deterministic() {
        let set = ValidatorSet::new(vec![validator(1), validator(2), validator(3)])
            .expect("validator set should be valid");

        let consensus = ConsensusValidator::new(set);

        assert_eq!(consensus.expected_proposer(1, 0).unwrap(), [2u8; 20]);
        assert_eq!(consensus.expected_proposer(1, 2).unwrap(), [1u8; 20]);
    }

    #[test]
    fn valid_proposer_is_accepted() {
        let set = ValidatorSet::new(vec![validator(1), validator(2), validator(3)])
            .expect("validator set should be valid");

        let consensus = ConsensusValidator::new(set);

        let block = Block::genesis(1, 1_700_000_000);

        // Height 0 + round 0 selects validator 1.
        assert_eq!(consensus.validate_proposer(&block, 0, &[1u8; 20],), Ok(()));
    }

    #[test]
    fn invalid_proposer_is_rejected() {
        let set = ValidatorSet::new(vec![validator(1), validator(2), validator(3)])
            .expect("validator set should be valid");

        let consensus = ConsensusValidator::new(set);

        let block = Block::genesis(1, 1_700_000_000);

        assert_eq!(
            consensus.validate_proposer(&block, 0, &[2u8; 20],),
            Err(ConsensusError::InvalidProposer)
        );
    }

    #[test]
    fn block_proposal_contains_block_hash() {
        let set = ValidatorSet::new(vec![validator(1)]).expect("validator set should be valid");

        let consensus = ConsensusValidator::new(set);

        let block = Block::genesis(1, 1_700_000_000);

        let proposal = BlockProposal::new(&block, 0, [1u8; 20]);

        assert_eq!(proposal.block_hash, block.hash());
        assert_eq!(proposal.height, 0);
        assert_eq!(proposal.round, 0);

        consensus
            .validate_proposal(&proposal)
            .expect("proposal should be valid");
    }

    #[test]
    fn consensus_round_is_constructible() {
        let round = ConsensusRound::new(10, 3);

        assert_eq!(round.height, 10);
        assert_eq!(round.round, 3);
    }
}
