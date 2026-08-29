//! Synora consensus primitives.
//!
//! Consensus is divided into three layers:
//!
//! - validator/proposal primitives
//! - vote accounting
//! - deterministic consensus state machine
//!
//! Networking, persistent consensus state, transaction execution, and
//! cryptographic vote signatures belong to higher layers.

pub mod engine;
pub mod vote;

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
    pub fn new(id: ValidatorId) -> Self {
        Self { id, power: 1 }
    }

    pub fn with_power(id: ValidatorId, power: u64) -> Self {
        Self { id, power }
    }

    pub fn is_valid(&self) -> bool {
        self.power > 0
    }
}

/// Deterministic validator set.
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

    /// Select deterministic proposer.
    ///
    /// ```text
    /// (height + round) % validator_count
    /// ```
    pub fn proposer(&self, height: u64, round: u64) -> Result<&Validator, ConsensusError> {
        if self.validators.is_empty() {
            return Err(ConsensusError::EmptyValidatorSet);
        }

        let index = ((height as usize) + (round as usize)) % self.validators.len();

        Ok(&self.validators[index])
    }

    /// Returns whether supplied voting power reaches
    /// Byzantine two-thirds quorum.
    pub fn has_quorum(&self, voting_power: u64) -> bool {
        let total = self.total_power();

        if total == 0 || voting_power > total {
            return false;
        }

        voting_power.saturating_mul(3) >= total.saturating_mul(2)
    }

    /// Minimum voting power required for quorum.
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

/// Validates proposals against the validator set.
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

    pub fn expected_proposer(
        &self,
        height: u64,
        round: u64,
    ) -> Result<ValidatorId, ConsensusError> {
        Ok(self.validator_set.proposer(height, round)?.id)
    }

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

    pub fn validate_proposal(&self, proposal: &BlockProposal) -> Result<(), ConsensusError> {
        let expected = self.expected_proposer(proposal.height, proposal.round)?;

        if proposal.proposer != expected {
            return Err(ConsensusError::InvalidProposer);
        }

        Ok(())
    }
}

/// Errors produced by consensus.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum ConsensusError {
    EmptyValidatorSet,
    DuplicateValidator,
    ValidatorNotFound,
    InvalidVotingPower,
    InvalidProposer,

    InvalidVoteHeight,
    InvalidVoteRound,
    InvalidVoteType,
    UnknownValidator,
    DuplicateVote,
    ConflictingVote,

    InvalidProposalHeight,
    InvalidProposalRound,
    DuplicateProposal,
    ProposalRequired,
    InvalidVoteBlock,

    PrevoteQuorumRequired,
    InvalidConsensusPhase,
    AlreadyCommitted,
}

impl std::fmt::Display for ConsensusError {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        let message = match self {
            Self::EmptyValidatorSet => "validator set is empty",

            Self::DuplicateValidator => "validator already exists",

            Self::ValidatorNotFound => "validator not found",

            Self::InvalidVotingPower => "validator voting power must be greater than zero",

            Self::InvalidProposer => "proposal proposer is not the expected proposer",

            Self::InvalidVoteHeight => "vote height does not match consensus height",

            Self::InvalidVoteRound => "vote round does not match consensus round",

            Self::InvalidVoteType => "vote type does not match vote set",

            Self::UnknownValidator => "vote validator is not in the validator set",

            Self::DuplicateVote => "validator already submitted this vote",

            Self::ConflictingVote => "validator submitted a conflicting vote",

            Self::InvalidProposalHeight => "proposal height does not match consensus height",

            Self::InvalidProposalRound => "proposal round does not match consensus round",

            Self::DuplicateProposal => "a proposal has already been accepted",

            Self::ProposalRequired => "a valid proposal is required",

            Self::InvalidVoteBlock => "vote block does not match the accepted proposal",

            Self::PrevoteQuorumRequired => "prevote quorum is required before precommit",

            Self::InvalidConsensusPhase => "operation is invalid for the current consensus phase",

            Self::AlreadyCommitted => "consensus height is already committed",
        };

        formatter.write_str(message)
    }
}

impl std::error::Error for ConsensusError {}

pub use engine::{CommitDecision, ConsensusEngine, ConsensusPhase};

pub use vote::{Vote, VoteSet, VoteType};
