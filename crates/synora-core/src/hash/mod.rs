use sha2::{Digest, Sha256};

pub type Hash = [u8; 32];

pub fn hash(data: &[u8]) -> Hash {
    let digest = Sha256::digest(data);

    let mut result = [0u8; 32];
    result.copy_from_slice(&digest);

    result
}

pub fn hash_pair(left: &Hash, right: &Hash) -> Hash {
    let mut data = [0u8; 64];

    data[..32].copy_from_slice(left);
    data[32..].copy_from_slice(right);

    hash(&data)
}

pub fn zero_hash() -> Hash {
    [0u8; 32]
}

pub fn to_hex(value: &Hash) -> String {
    value.iter().map(|byte| format!("{byte:02x}")).collect()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn hash_is_deterministic() {
        let first = hash(b"synora");
        let second = hash(b"synora");

        assert_eq!(first, second);
    }

    #[test]
    fn different_data_has_different_hash() {
        let first = hash(b"synora");
        let second = hash(b"synora-chain");

        assert_ne!(first, second);
    }

    #[test]
    fn hash_is_32_bytes() {
        let result = hash(b"synora");

        assert_eq!(result.len(), 32);
    }

    #[test]
    fn pair_hash_is_deterministic() {
        let left = hash(b"left");
        let right = hash(b"right");

        assert_eq!(hash_pair(&left, &right), hash_pair(&left, &right));
    }

    #[test]
    fn zero_hash_is_zero() {
        assert_eq!(zero_hash(), [0u8; 32]);
    }

    #[test]
    fn hex_conversion_works() {
        let value = [0xabu8; 32];
        let result = to_hex(&value);

        assert_eq!(result.len(), 64);
        assert!(result.chars().all(|c| c == 'a' || c == 'b'));
    }
}
