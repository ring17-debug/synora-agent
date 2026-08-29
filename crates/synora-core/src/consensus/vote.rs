//! Consensus voting primitives.
//!
//! This module contains deterministic vote structures and vote-set
//! accounting. Networking and vote propagation belong to higher layers.

use super::{ConsensusError, ConsensusRound, ValidatorId, ValidatorSet};
use crate::hash::Hash;

/// Type of consensus vote.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub enum VoteType {
    /// First-stage vote for a proposed block.
    Prevote,

    /// Second-stage vote indicating commitment readiness.
    Precommit,
}

/// A signed-consensus-compatible vote payload.
///
/// Cryptographic vote signatures are intentionally handled by a higher
/// layer for now. This structure represents the consensus payload that
/// must be validated before signature verification is applied.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Vote {
    pub height: u64,
    pub round: u64,
    pub block_hash: Hash,
    pub validator: ValidatorId,
    pub vote_type: VoteType,
}

impl Vote {
    pub fn new(
        height: u64,
        round: u64,
        block_hash: Hash,
        validator: ValidatorId,
        vote_type: VoteType,
    ) -> Self {
        Self {
            height,
            round,
            block_hash,
            validator,
            vote_type,
        }
    }

    pub fn round(&self) -> ConsensusRound {
        ConsensusRound::new(self.height, self.round)
    }
}

/// A collection of votes for one consensus round and vote type.
///
/// A validator may only contribute its voting power once to a given
/// vote type. Conflicting votes from the same validator are rejected.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct VoteSet {
    height: u64,
    round: u64,
    vote_type: VoteType,
    votes: Vec<Vote>,
}

impl VoteSet {
    pub fn new(height: u64, round: u64, vote_type: VoteType) -> Self {
        Self {
            height,
            round,
            vote_type,
            votes: Vec::new(),
        }
    }

    pub fn height(&self) -> u64 {
        self.height
    }

    pub fn round(&self) -> u64 {
        self.round
    }

    pub fn vote_type(&self) -> VoteType {
        self.vote_type
    }

    pub fn votes(&self) -> &[Vote] {
        &self.votes
    }

    pub fn len(&self) -> usize {
        self.votes.len()
    }

    pub fn is_empty(&self) -> bool {
        self.votes.is_empty()
    }

    pub fn contains_validator(&self, validator: &ValidatorId) -> bool {
        self.votes.iter().any(|vote| &vote.validator == validator)
    }

    /// Returns whether all votes in the set support the same block.
    pub fn block_hash(&self) -> Option<Hash> {
        self.votes.first().map(|vote| vote.block_hash)
    }

    /// Returns the total voting power represented by this vote set.
    ///
    /// Unknown validators are ignored here because validation happens in
    /// `insert`. This method therefore remains a pure accounting operation.
    pub fn voting_power(&self, validators: &ValidatorSet) -> u64 {
        self.votes
            .iter()
            .filter_map(|vote| validators.get(&vote.validator))
            .map(|validator| validator.power)
            .sum()
    }

    /// Returns whether this vote set has reached two-thirds quorum.
    pub fn has_quorum(&self, validators: &ValidatorSet) -> bool {
        validators.has_quorum(self.voting_power(validators))
    }

    /// Insert and validate a vote.
    pub fn insert(&mut self, vote: Vote, validators: &ValidatorSet) -> Result<(), ConsensusError> {
        if vote.height != self.height {
            return Err(ConsensusError::InvalidVoteHeight);
        }

        if vote.round != self.round {
            return Err(ConsensusError::InvalidVoteRound);
        }

        if vote.vote_type != self.vote_type {
            return Err(ConsensusError::InvalidVoteType);
        }

        if !validators.contains(&vote.validator) {
            return Err(ConsensusError::UnknownValidator);
        }

        if self.contains_validator(&vote.validator) {
            let existing = self
                .votes
                .iter()
                .find(|item| item.validator == vote.validator)
                .expect("validator was already found");

            if existing.block_hash != vote.block_hash {
                return Err(ConsensusError::ConflictingVote);
            }

            return Err(ConsensusError::DuplicateVote);
        }

        self.votes.push(vote);

        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn validator(value: u8) -> ValidatorId {
        [value; 20]
    }

    fn validators() -> ValidatorSet {
        ValidatorSet::new(vec![
            super::super::Validator::with_power(validator(1), 1),
            super::super::Validator::with_power(validator(2), 1),
            super::super::Validator::with_power(validator(3), 1),
        ])
        .expect("validator set should be valid")
    }

    fn hash(value: u8) -> Hash {
        [value; 32]
    }

    #[test]
    fn vote_can_be_created() {
        let vote = Vote::new(10, 2, hash(1), validator(1), VoteType::Prevote);

        assert_eq!(vote.height, 10);
        assert_eq!(vote.round, 2);
        assert_eq!(vote.block_hash, hash(1));
        assert_eq!(vote.validator, validator(1));
        assert_eq!(vote.vote_type, VoteType::Prevote);
    }

    #[test]
    fn vote_round_is_constructible() {
        let vote = Vote::new(10, 2, hash(1), validator(1), VoteType::Prevote);

        assert_eq!(vote.round(), ConsensusRound::new(10, 2));
    }

    #[test]
    fn vote_set_accepts_valid_vote() {
        let validators = validators();

        let mut votes = VoteSet::new(1, 0, VoteType::Prevote);

        votes
            .insert(
                Vote::new(1, 0, hash(1), validator(1), VoteType::Prevote),
                &validators,
            )
            .expect("vote should be accepted");

        assert_eq!(votes.len(), 1);
        assert_eq!(votes.voting_power(&validators), 1);
    }

    #[test]
    fn duplicate_vote_is_rejected() {
        let validators = validators();

        let mut votes = VoteSet::new(1, 0, VoteType::Prevote);

        let vote = Vote::new(1, 0, hash(1), validator(1), VoteType::Prevote);

        votes
            .insert(vote.clone(), &validators)
            .expect("first vote should work");

        assert_eq!(
            votes.insert(vote, &validators),
            Err(ConsensusError::DuplicateVote)
        );
    }

    #[test]
    fn conflicting_vote_is_rejected() {
        let validators = validators();

        let mut votes = VoteSet::new(1, 0, VoteType::Prevote);

        votes
            .insert(
                Vote::new(1, 0, hash(1), validator(1), VoteType::Prevote),
                &validators,
            )
            .expect("first vote should work");

        assert_eq!(
            votes.insert(
                Vote::new(1, 0, hash(2), validator(1), VoteType::Prevote,),
                &validators,
            ),
            Err(ConsensusError::ConflictingVote)
        );
    }

    #[test]
    fn unknown_validator_is_rejected() {
        let validators = validators();

        let mut votes = VoteSet::new(1, 0, VoteType::Prevote);

        assert_eq!(
            votes.insert(
                Vote::new(1, 0, hash(1), validator(9), VoteType::Prevote,),
                &validators,
            ),
            Err(ConsensusError::UnknownValidator)
        );
    }

    #[test]
    fn wrong_height_is_rejected() {
        let validators = validators();

        let mut votes = VoteSet::new(1, 0, VoteType::Prevote);

        assert_eq!(
            votes.insert(
                Vote::new(2, 0, hash(1), validator(1), VoteType::Prevote,),
                &validators,
            ),
            Err(ConsensusError::InvalidVoteHeight)
        );
    }

    #[test]
    fn wrong_round_is_rejected() {
        let validators = validators();

        let mut votes = VoteSet::new(1, 0, VoteType::Prevote);

        assert_eq!(
            votes.insert(
                Vote::new(1, 1, hash(1), validator(1), VoteType::Prevote,),
                &validators,
            ),
            Err(ConsensusError::InvalidVoteRound)
        );
    }

    #[test]
    fn wrong_vote_type_is_rejected() {
        let validators = validators();

        let mut votes = VoteSet::new(1, 0, VoteType::Prevote);

        assert_eq!(
            votes.insert(
                Vote::new(1, 0, hash(1), validator(1), VoteType::Precommit,),
                &validators,
            ),
            Err(ConsensusError::InvalidVoteType)
        );
    }

    #[test]
    fn quorum_is_reached_at_two_thirds() {
        let validators = validators();

        let mut votes = VoteSet::new(1, 0, VoteType::Prevote);

        votes
            .insert(
                Vote::new(1, 0, hash(1), validator(1), VoteType::Prevote),
                &validators,
            )
            .unwrap();

        assert!(!votes.has_quorum(&validators));

        votes
            .insert(
                Vote::new(1, 0, hash(1), validator(2), VoteType::Prevote),
                &validators,
            )
            .unwrap();

        assert!(votes.has_quorum(&validators));
    }

    #[test]
    fn block_hash_is_consistent() {
        let validators = validators();

        let mut votes = VoteSet::new(1, 0, VoteType::Prevote);

        assert_eq!(votes.block_hash(), None);

        votes
            .insert(
                Vote::new(1, 0, hash(7), validator(1), VoteType::Prevote),
                &validators,
            )
            .unwrap();

        assert_eq!(votes.block_hash(), Some(hash(7)));
    }
}
